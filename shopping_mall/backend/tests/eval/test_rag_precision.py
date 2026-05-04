"""Experiment B - RAG Precision@3: Dense-only vs Hybrid (Dense + BM25 + RRF)

측정 목표
----------
동일한 쿼리에 대해 Dense 단독 검색과 Hybrid 검색의 Precision@3을 비교하여
BM25 통합이 실제로 검색 품질을 향상시키는지를 정량화합니다.

관련성 판단 방식
----------------
전문가 레이블링 대신 relevant_keywords 기반 근사 측정을 사용합니다.
retrieved 문서가 관련 키워드를 하나 이상 포함하면 relevant로 판단합니다.
이는 annotation 비용 없이 방향성 비교가 가능한 표준적 접근입니다.

수식:
  Precision@k = |{d in top_k : d is relevant}| / k
  relevant(d) = any(kw in d.lower() for kw in relevant_keywords)

비교 대상
----------
- Dense-only : RAGService.retrieve_multiple() - 컬렉션별 Dense 검색 + 거리 정렬
- Hybrid     : RAGService.hybrid_retrieve()   - Dense + BM25 + RRF 합산

BM25 강점 케이스 (eval_dataset.json::rag::group="bm25_strong"):
  정확한 키워드(조항 번호, 상품명, 속성명)가 포함된 쿼리 - BM25 lexical 검색 강점

Dense 강점 케이스 (group="dense_strong"):
  구어체/상황 묘사 쿼리 - bge-m3 시맨틱 임베딩 강점

측정 결과 (N=20, 2026-04-30 기준)
-----------------------------------
  Dense-only  avg P@3 : 0.933 (93.3%)
  Hybrid      avg P@3 : 0.933 (93.3%)
  전체 격차           : 0.0%p  (Hybrid wins=1, Dense wins=1, tie=18)

  그룹별:
    BM25 strong (N=8) : Dense 0.958  Hybrid 0.958  delta=+0.0%p
    Dense strong (N=7): Dense 0.905  Hybrid 0.857  delta=-4.8%p
    Mixed        (N=5): Dense 0.933  Hybrid 1.000  delta=+6.7%p

  주요 관찰:
  - 정책·FAQ 혼합 쿼리(Mixed)에서 Hybrid +6.7%p 우세
  - 구어체 시맨틱 쿼리(Dense strong)에서 Dense +4.8%p 우세
  - 조항 번호/상품명 키워드 쿼리(BM25 strong)는 두 방법 동률

tokenize_ko 개선 이력
----------------------
초기 단순 정규식 토크나이저(공백 분리)에서 어미·조사 제거 방식으로 개선 (v2).

  개선 전: "주문한 물건이 안 와요" → ['주문한', '물건이', '안', '와요']
           BM25 토큰 불일치 → Hybrid avg P@3 = 0.883 (Dense 대비 -5.0%p)

  개선 후: "주문한 물건이 안 와요" → ['주문', '물건']
           한국어 교착어 어미/조사 제거 → Hybrid avg P@3 = 0.933 (Dense 동률)

  한국어 BM25는 조항 구조와 무관하게 토큰 레벨에서 정확한 매칭이 필요합니다.
  문서의 "기간은"과 쿼리의 "기간이"는 동일 조사 없이는 일치하지 않습니다.

실행 요구사항
--------------
ChromaDB + BM25 인덱스가 시딩된 환경에서만 실행됩니다.
미시딩 시 모든 테스트가 자동으로 SKIP됩니다.

  # 시딩 방법
  cd shopping_mall/backend
  uv run python ai/seed_rag.py

  # BM25 인덱스만 재빌드 (ChromaDB 유지, tokenize_ko 변경 후 필수)
  uv run python -c "
  import json, chromadb
  from app.paths import CHROMA_DB_PATH, AI_DATA_DIR
  from ai.utils import tokenize_ko
  client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
  # ... (seed_rag.build_bm25_index 참고)
  "

  # 실험 실행
  uv run pytest tests/eval/test_rag_precision.py -v -s
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

# ── 데이터셋 / 컬렉션 정의 ─────────────────────────────────────────────────────

_DATASET_PATH = Path(__file__).parent / "eval_dataset.json"

# 전체 컬렉션 목록 (seed_rag._ALL_COLLECTIONS 와 동일)
_ALL_COLLECTIONS = [
    "faq",
    "payment_policy",
    "delivery_policy",
    "return_policy",
    "quality_policy",
    "service_policy",
    "membership_policy",
]


def _required_rag_collections() -> list[str]:
    """이 평가셋이 직접 조회하는 RAG 컬렉션 목록."""
    data = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    required: set[str] = set()
    for case in data.get("rag", []):
        valid_cols = [col for col in case.get("collections", []) if col in _ALL_COLLECTIONS]
        required.update(valid_cols or _ALL_COLLECTIONS)
    return sorted(required or _ALL_COLLECTIONS)


def _find_unavailable_collections(rag: "RAGService", collections: list[str]) -> list[str]:
    """컬렉션이 없거나 비어 있으면 RAG 평가를 SKIP하기 위한 결함 목록."""
    unavailable = []
    for name in collections:
        col = rag._get_collection(name)
        if col is None:
            unavailable.append(name)
            continue
        try:
            if col.count() == 0:
                unavailable.append(f"{name}(empty)")
        except Exception:
            unavailable.append(f"{name}(count_failed)")
    return unavailable


# ── RAG 환경 가용성 체크 ───────────────────────────────────────────────────────
# 모듈 로드 시 1회 체크 — ChromaDB 미시딩 시 전체 SKIP

_rag_service = None
_rag_skip_reason = ""

try:
    # Windows + PyTorch OpenMP 충돌 방지 (conftest.py에서도 설정하지만 단독 실행 대비 이중 방어)
    if sys.platform.startswith("win"):
        os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"

    from ai.rag import RAGService
    _svc = RAGService()

    if _svc.chroma_client is None:
        _rag_skip_reason = "ChromaDB 초기화 실패. uv run python ai/seed_rag.py 를 먼저 실행하세요."
    else:
        _unavailable = _find_unavailable_collections(_svc, _required_rag_collections())
        if _unavailable:
            _rag_skip_reason = (
                f"미시딩 또는 빈 컬렉션: {', '.join(_unavailable)}. "
                "uv run python ai/seed_rag.py 를 먼저 실행하세요."
            )
        else:
            _rag_service = _svc

except Exception as e:
    _rag_skip_reason = f"RAGService 로드 실패: {e}"

requires_rag = pytest.mark.skipif(
    _rag_service is None,
    reason=_rag_skip_reason or "RAG 환경 없음",
)

@dataclass
class RagCase:
    id: int
    query: str
    relevant_keywords: list[str]
    collections: list[str]
    note: str
    group: str = "mixed"


def _load_rag_cases() -> list[RagCase]:
    data = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    cases = []
    for c in data.get("rag", []):
        # 데이터셋에 명시된 컬렉션이 _ALL_COLLECTIONS에 포함된 것만 사용
        valid_cols = [col for col in c["collections"] if col in _ALL_COLLECTIONS]
        # 컬렉션 미지정이면 전체 컬렉션 사용
        cols = valid_cols if valid_cols else _ALL_COLLECTIONS
        cases.append(RagCase(
            id=c["id"],
            query=c["query"],
            relevant_keywords=[kw.lower() for kw in c["relevant_keywords"]],
            collections=cols,
            note=c["note"],
            group=_classify_group(c),
        ))
    return cases


def _classify_group(case_dict: dict) -> str:
    """note 기반으로 BM25 강점 / Dense 강점 / mixed 분류."""
    note = case_dict.get("note", "").lower()
    if "bm25 강점" in note or "bm25 최강점" in note:
        return "bm25_strong"
    if "dense 강점" in note:
        return "dense_strong"
    return "mixed"


# ── Precision@k 계산 ──────────────────────────────────────────────────────────

def precision_at_k(docs: list[str], relevant_keywords: list[str], k: int = 3) -> float:
    """상위 k개 문서 중 관련 문서 비율.

    관련성 판단: 문서가 relevant_keywords 중 하나 이상을 포함하면 relevant.
    docs가 k보다 적으면 실제 개수 기준으로 계산 (분모 = k 고정으로 불이익 부여).

    Args:
        docs: 검색된 문서 리스트 (이미 정렬됨)
        relevant_keywords: 소문자 정규화된 관련 키워드 리스트
        k: Precision 계산 기준 상위 k개

    Returns:
        0.0 ~ 1.0 사이의 Precision@k 값
    """
    if not docs:
        return 0.0
    hits = 0
    for doc in docs[:k]:
        doc_lower = doc.lower()
        if any(kw in doc_lower for kw in relevant_keywords):
            hits += 1
    return hits / k


# ── 검색 래퍼 ────────────────────────────────────────────────────────────────

def dense_retrieve(rag: "RAGService", query: str, collections: list[str], top_k: int = 3) -> list[str]:
    """Dense-only 검색 — retrieve_multiple() 사용.

    컬렉션별 Dense 검색 결과를 거리 기준으로 병합하여 상위 top_k 반환.
    top_k_per를 top_k와 동일하게 설정하여 각 컬렉션에서 충분한 후보를 수집.
    """
    from ai.rag import normalize_query
    nq = normalize_query(query)
    docs = rag.retrieve_multiple(
        question=nq,
        collections=collections,
        top_k_per=top_k,
        distance_threshold=0.5,
    )
    return docs[:top_k]


def hybrid_retrieve(rag: "RAGService", query: str, collections: list[str], top_k: int = 3) -> list[str]:
    """Hybrid 검색 — hybrid_retrieve() 사용 (Dense + BM25 + RRF).

    normalize_query()는 hybrid_retrieve() 내부에서 적용되므로 원본 쿼리 전달.
    """
    return rag.hybrid_retrieve(
        question=query,
        collections=collections,
        top_k=top_k,
        distance_threshold=0.5,
    )


# ── 결과 집계 ────────────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    case: RagCase
    dense_docs: list[str]
    hybrid_docs: list[str]
    dense_p3: float
    hybrid_p3: float

    @property
    def winner(self) -> str:
        if self.hybrid_p3 > self.dense_p3:
            return "Hybrid"
        if self.dense_p3 > self.hybrid_p3:
            return "Dense"
        return "tie"


def _run_evaluation(rag: "RAGService", top_k: int = 3) -> list[CaseResult]:
    cases = _load_rag_cases()
    results = []
    for c in cases:
        d_docs = dense_retrieve(rag, c.query, c.collections, top_k)
        h_docs = hybrid_retrieve(rag, c.query, c.collections, top_k)
        results.append(CaseResult(
            case=c,
            dense_docs=d_docs,
            hybrid_docs=h_docs,
            dense_p3=precision_at_k(d_docs, c.relevant_keywords, top_k),
            hybrid_p3=precision_at_k(h_docs, c.relevant_keywords, top_k),
        ))
    return results


def _print_report(results: list[CaseResult], top_k: int = 3) -> None:
    n = len(results)
    avg_dense = sum(r.dense_p3 for r in results) / n
    avg_hybrid = sum(r.hybrid_p3 for r in results) / n
    hybrid_wins = sum(1 for r in results if r.winner == "Hybrid")
    dense_wins = sum(1 for r in results if r.winner == "Dense")
    ties = sum(1 for r in results if r.winner == "tie")

    print(f"\n{'='*72}")
    print(f"  Experiment B - RAG Precision@{top_k} Comparison  (N={n})")
    print(f"{'='*72}")
    print(f"  Dense-only  : avg P@{top_k} = {avg_dense:.3f}")
    print(f"  Hybrid      : avg P@{top_k} = {avg_hybrid:.3f}")
    delta = (avg_hybrid - avg_dense) * 100
    sign = "+" if delta >= 0 else ""
    print(f"  Improvement : {sign}{delta:.1f}%p  "
          f"(Hybrid wins={hybrid_wins}, Dense wins={dense_wins}, tie={ties})\n")

    # 그룹별 요약
    groups = {"bm25_strong": [], "dense_strong": [], "mixed": []}
    for r in results:
        groups[r.case.group].append(r)

    for group_name, group_results in groups.items():
        if not group_results:
            continue
        gd = sum(r.dense_p3 for r in group_results) / len(group_results)
        gh = sum(r.hybrid_p3 for r in group_results) / len(group_results)
        gdelta = (gh - gd) * 100
        gsign = "+" if gdelta >= 0 else ""
        label = {
            "bm25_strong": "BM25 strong cases",
            "dense_strong": "Dense strong cases",
            "mixed": "Mixed cases",
        }[group_name]
        print(f"  [{label}] N={len(group_results)}")
        print(f"    Dense: {gd:.3f}  Hybrid: {gh:.3f}  delta={gsign}{gdelta:.1f}%p")

    print()

    # 케이스별 상세
    print(f"  {'#':>3}  {'Query':<35}  {'Dense':>6}  {'Hybrid':>6}  Winner")
    print(f"  {'-'*3}  {'-'*35}  {'-'*6}  {'-'*6}  {'-'*6}")
    for r in results:
        q = r.case.query
        if len(q) > 33:
            q = q[:30] + "..."
        winner_mark = "" if r.winner == "tie" else ("(*)" if r.winner == "Hybrid" else "   ")
        print(f"  #{r.case.id:02d}  {q:<35}  {r.dense_p3:.3f}   {r.hybrid_p3:.3f}   {r.winner}{winner_mark}")

    print(f"{'='*72}\n")


# ── 전체 요약 테스트 ─────────────────────────────────────────────────────────

class TestRagPrecisionSummary:
    """전체 요약 - pytest -v -s 로 리포트 출력."""

    @requires_rag
    def test_print_full_report(self, capsys):
        """전체 평가 리포트를 출력하고 기본 무결성을 검증합니다."""
        results = _run_evaluation(_rag_service)
        _print_report(results)

        n = len(results)
        assert n == 20, f"테스트셋 크기 불일치: {n} (기대값 20)"

        avg_dense = sum(r.dense_p3 for r in results) / n
        avg_hybrid = sum(r.hybrid_p3 for r in results) / n

        # 두 방법 모두 완전히 0이면 시딩 문제
        assert avg_dense > 0 or avg_hybrid > 0, (
            "Dense/Hybrid 모두 P@3=0. 컬렉션이 비어 있거나 임계값이 너무 낮을 수 있습니다."
        )

        # Hybrid가 Dense보다 낮으면 실험 설계 이상 경고
        if avg_hybrid < avg_dense - 0.05:
            import warnings
            warnings.warn(
                f"Hybrid P@3({avg_hybrid:.3f}) < Dense P@3({avg_dense:.3f}). "
                "BM25 인덱스가 비어 있거나 distance_threshold 조정이 필요합니다."
            )

    @requires_rag
    def test_hybrid_not_worse_than_dense_overall(self):
        """Hybrid의 평균 P@3이 Dense보다 나쁘지 않아야 합니다 (5%p 허용 마진)."""
        results = _run_evaluation(_rag_service)
        n = len(results)
        avg_dense = sum(r.dense_p3 for r in results) / n
        avg_hybrid = sum(r.hybrid_p3 for r in results) / n

        assert avg_hybrid >= avg_dense - 0.05, (
            f"Hybrid avg P@3 ({avg_hybrid:.3f}) < Dense avg P@3 ({avg_dense:.3f}) - 5%p 마진 초과. "
            "BM25 인덱스 또는 RRF 파라미터를 확인하세요."
        )


# ── BM25 강점 케이스 테스트 ────────────────────────────────────────────────────

class TestBm25StrongCases:
    """BM25 강점 케이스: 정확한 키워드 포함 쿼리에서 Hybrid가 Dense 이상이어야 함."""

    @requires_rag
    def test_bm25_strong_hybrid_advantage(self):
        """BM25 강점 케이스에서 Hybrid가 Dense 이상의 P@3을 기록해야 합니다."""
        results = _run_evaluation(_rag_service)
        bm25_cases = [r for r in results if r.case.group == "bm25_strong"]

        if not bm25_cases:
            pytest.skip("bm25_strong 그룹 케이스 없음")

        avg_dense = sum(r.dense_p3 for r in bm25_cases) / len(bm25_cases)
        avg_hybrid = sum(r.hybrid_p3 for r in bm25_cases) / len(bm25_cases)

        assert avg_hybrid >= avg_dense, (
            f"BM25 강점 케이스에서 Hybrid({avg_hybrid:.3f}) < Dense({avg_dense:.3f}). "
            f"BM25 인덱스가 시딩되었는지 확인하세요."
        )

    @requires_rag
    @pytest.mark.parametrize("case", [
        c for c in _load_rag_cases() if c.group == "bm25_strong"
    ], ids=lambda c: f"#{c.id}_{c.query[:20]}")
    def test_bm25_case_has_results(self, case: RagCase):
        """BM25 강점 케이스 각각에서 최소 1개 이상의 문서가 검색되어야 합니다."""
        h_docs = hybrid_retrieve(_rag_service, case.query, case.collections)
        assert len(h_docs) >= 1, (
            f"#{case.id} '{case.query}' - Hybrid 검색 결과 없음. "
            f"컬렉션 {case.collections} 확인 필요."
        )


# ── Dense 강점 케이스 테스트 ───────────────────────────────────────────────────

class TestDenseStrongCases:
    """Dense 강점 케이스: 구어체/시맨틱 쿼리에서 Dense가 일정 수준의 결과를 내야 함."""

    @requires_rag
    @pytest.mark.parametrize("case", [
        c for c in _load_rag_cases() if c.group == "dense_strong"
    ], ids=lambda c: f"#{c.id}_{c.query[:20]}")
    def test_dense_case_has_semantic_results(self, case: RagCase):
        """Dense 강점 케이스에서 Dense 검색이 최소 1개 문서를 반환해야 합니다."""
        d_docs = dense_retrieve(_rag_service, case.query, case.collections)
        assert len(d_docs) >= 1, (
            f"#{case.id} '{case.query}' - Dense 검색 결과 없음. "
            f"ChromaDB 임베딩 상태를 확인하세요."
        )


# ── 개별 케이스 Precision@3 테스트 ─────────────────────────────────────────────

class TestIndividualPrecision:
    """개별 케이스 P@3 검증 - 심각한 이상값 감지용."""

    @requires_rag
    @pytest.mark.parametrize("case", _load_rag_cases(),
                             ids=lambda c: f"#{c.id}_{c.query[:20]}")
    def test_at_least_one_method_finds_results(self, case: RagCase):
        """각 케이스에서 Dense 또는 Hybrid 중 하나 이상이 결과를 반환해야 합니다."""
        d_docs = dense_retrieve(_rag_service, case.query, case.collections)
        h_docs = hybrid_retrieve(_rag_service, case.query, case.collections)

        assert len(d_docs) > 0 or len(h_docs) > 0, (
            f"#{case.id} '{case.query}' - 두 방법 모두 결과 없음. "
            f"컬렉션={case.collections}, 임계값=0.5 확인 필요."
        )


# ── Precision@k 헬퍼 단위 테스트 ─────────────────────────────────────────────

class TestPrecisionAtKUnit:
    """precision_at_k() 헬퍼 함수 검증 - RAG 환경 불필요."""

    def test_all_relevant(self):
        docs = ["반품 기간은 7일입니다", "반품 절차 안내", "반품 조건 확인"]
        assert precision_at_k(docs, ["반품"], k=3) == pytest.approx(1.0)

    def test_none_relevant(self):
        docs = ["배송 안내", "결제 방법", "회원 가입"]
        assert precision_at_k(docs, ["반품"], k=3) == pytest.approx(0.0)

    def test_partial_relevant(self):
        docs = ["반품 기간은 7일", "배송 안내", "반품 절차"]
        # 3개 중 2개 relevant -> 2/3
        assert precision_at_k(docs, ["반품"], k=3) == pytest.approx(2 / 3)

    def test_fewer_docs_than_k(self):
        """검색 결과가 k보다 적으면 분모는 k 고정 (불이익 부여)."""
        docs = ["반품 기간은 7일"]  # 1개 반환
        # 1/3 (분모 k=3 고정)
        assert precision_at_k(docs, ["반품"], k=3) == pytest.approx(1 / 3)

    def test_empty_docs(self):
        assert precision_at_k([], ["반품"], k=3) == pytest.approx(0.0)

    def test_keyword_case_insensitive(self):
        """대소문자 구분 없이 키워드 매칭."""
        docs = ["Return policy: 7 days"]
        assert precision_at_k(docs, ["return"], k=3) == pytest.approx(1 / 3)

    def test_multiple_keywords_any_match(self):
        """relevant_keywords 중 하나만 있어도 relevant."""
        docs = ["배송비는 무료입니다", "반품 기간 안내"]
        # 첫 번째 doc: "배송" 포함 -> relevant
        # 두 번째 doc: "반품" 포함 -> relevant
        # 분모 k=3 고정 -> 2/3
        assert precision_at_k(docs, ["반품", "배송"], k=3) == pytest.approx(2 / 3)
