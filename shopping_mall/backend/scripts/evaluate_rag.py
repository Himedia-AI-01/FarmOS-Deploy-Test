from __future__ import annotations  # must be the very first statement

# Windows OpenMP DLL 충돌 방지 — 반드시 torch/sentence_transformers 임포트 전에 설정
# bge-m3 임베딩 + CrossEncoder 리랭커가 각각 torch를 로딩할 때 libiomp5md.dll 충돌 발생
import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"   # OpenMP 스레드 단일화 → DLL 충돌 원천 차단

"""RAG 파이프라인 성능 평가 스크립트 v2 — cs_tools 레벨.

Supervisor LLM이 이미 라우팅 결정을 완료했다고 가정하고,
실제 프로덕션 함수(search_faq / search_policy)를 직접 호출하여 RAG 품질을 측정합니다.

측정 지표
─────────
  Hit Rate@k  : 반환 텍스트에 정답 키워드가 포함된 비율
  Mean Latency: 검색 1회 평균 응답 시간 (ms)
  P95 Latency : 95th-percentile 응답 시간 (ms)

Baseline vs Improved 차이
──────────────────────────
  FAQ 쿼리   : normalize_query 적용 여부 + subcategory 메타 필터
  Policy 쿼리: normalize_query + hybrid(Dense+BM25+RRF) + Cross-Encoder Reranking

골든셋 설계 원칙
────────────────
  tool / tool_args = Supervisor LLM이 결정했을 도구 호출을 하드코딩으로 시뮬레이션
  query = 사용자 원문 그대로 (normalize 전)

실행:
    cd shopping_mall/backend
    uv run python scripts/evaluate_rag.py
    uv run python scripts/evaluate_rag.py --no-rerank
    uv run python scripts/evaluate_rag.py --export results.json
    uv run python scripts/evaluate_rag.py --verbose
"""
import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from statistics import mean, median, quantiles

sys.stdout.reconfigure(encoding="utf-8")
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _BACKEND_DIR)

from app.core.config import settings
from ai.rag import RAGService
from ai.agent.cs_tools import build_cs_tools, POLICY_COLLECTIONS


# ─────────────────────────────────────────────────────────────────────────────
# 골든셋 정의
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GoldenItem:
    """평가 단위.

    query      : 사용자 입력 원문 (정규화·LLM 처리 전)
    tool       : 프로덕션 cs_tool 이름 ("search_faq" | "search_policy")
    tool_args  : 도구 인자 — LLM 라우팅 결정을 하드코딩으로 시뮬레이션
                   search_faq  → {"subcategory": "delivery"} 등
                   search_policy → {"policy_type": "return"} 등
    keywords   : 반환 텍스트에 포함되어야 할 키워드 (1개 이상 = relevant)
    query_type : 유형 레이블 (리포트용)
    """
    query: str
    tool: str
    tool_args: dict
    keywords: list[str]
    query_type: str = "direct"


GOLDEN_SET: list[GoldenItem] = [
    # ══════════════════════════════════════════════════════════════════════════
    # EASY  (5개) — 광의 키워드, Dense도 잘 처리
    #              Baseline과 Improved 모두 통과해 기반 역량을 증명
    # ══════════════════════════════════════════════════════════════════════════

    # 구어체 — normalize_query가 "택배비→배송비" 치환 후 Dense 검색
    GoldenItem(
        query="택배비 기준이 어떻게 돼요?",
        tool="search_policy", tool_args={"policy_type": "delivery"},
        keywords=["배송비", "무료"],
        query_type="colloquial",
    ),
    # 구어체 — "환불"을 그대로 써도 Dense가 의미적으로 반품 정책 청크를 올려옴
    GoldenItem(
        query="환불 받는데 얼마나 걸려요?",
        tool="search_policy", tool_args={"policy_type": "return"},
        keywords=["환불", "영업일"],
        query_type="colloquial",
    ),
    # 동의어 — "마일리지→적립금" normalize 효과 + Dense 의미 유사성 둘 다 동작
    GoldenItem(
        query="마일리지로 결제하면 어떻게 되나요?",
        tool="search_policy", tool_args={"policy_type": "membership"},
        keywords=["적립금", "포인트"],
        query_type="synonym",
    ),
    # 멀티 의도 — _split_query로 분리된 두 서브쿼리 모두 Dense로 처리 가능
    GoldenItem(
        query="배송 기간이 얼마나 걸리고 반품은 어떻게 하나요?",
        tool="search_policy", tool_args={"policy_type": "all"},
        keywords=["배송", "반품"],
        query_type="multi_intent",
    ),
    # 직접 질문 — 의미 유사도가 충분히 높아 Dense도 rank 1 유지
    GoldenItem(
        query="신선도 보증 기간이 어떻게 되나요?",
        tool="search_policy", tool_args={"policy_type": "quality"},
        keywords=["보증", "신선"],
        query_type="direct",
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # MEDIUM  (5개) — 정확한 수치·기간 키워드
    #                 Dense는 의미상 올바른 청크를 가져오지만 rank 1 보장 어려움
    #                 BM25 exact-match가 해당 청크를 상위로 끌어올림
    # ══════════════════════════════════════════════════════════════════════════

    # 수치 키워드: "7일" — 반품 청크마다 일부 포함되나, 정확한 조항 청크가 rank 1이어야 함
    GoldenItem(
        query="반품 신청 가능 기간이 며칠인가요?",
        tool="search_policy", tool_args={"policy_type": "return"},
        keywords=["7일"],
        query_type="exact_number",
    ),
    # 수치 키워드: "24시간" — 신선식품 신고 마감 시한 (return/quality 교차)
    GoldenItem(
        query="신선식품 받고 나서 품질 문제 신고를 얼마나 빨리 해야 하나요?",
        tool="search_policy", tool_args={"policy_type": "return"},
        keywords=["24시간"],
        query_type="exact_number",
    ),
    # 수치 키워드: "12개월" — 포인트 유효기간 (generic "기간" 쿼리로 Dense 분산 유발)
    GoldenItem(
        query="포인트 유효기간이 어떻게 되나요?",
        tool="search_policy", tool_args={"policy_type": "membership"},
        keywords=["12개월"],
        query_type="exact_number",
    ),
    # 수치 키워드: "30일" — 탈퇴 후 재가입 제한 (회원 정책 중 비교적 비주류 조항)
    GoldenItem(
        query="탈퇴하고 나서 바로 다시 가입할 수 있나요?",
        tool="search_policy", tool_args={"policy_type": "membership"},
        keywords=["30일"],
        query_type="exact_number",
    ),
    # 수치 키워드: "3일" + "3,000원" — 배송 지연 보상 기준, 복수 수치 동시 매칭
    GoldenItem(
        query="배송이 늦어지면 어떻게 보상받나요?",
        tool="search_policy", tool_args={"policy_type": "return"},
        keywords=["3일", "3,000원"],
        query_type="exact_number",
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # HARD  (5개) — 약어·희소 복합명사·고정밀 수치
    #               Dense 임베딩이 의미 희석 or OOV 처리로 정밀도 저하
    #               BM25 exact-match + Cross-Encoder rerank 없이는 rank 1 불안정
    # ══════════════════════════════════════════════════════════════════════════

    # 약어(OOV): "GAP" — 영문 약어, Dense는 의미 공간에서 floats 처리
    GoldenItem(
        query="인증 받은 농가에서 납품받는 건가요?",
        tool="search_policy", tool_args={"policy_type": "quality"},
        keywords=["GAP"],
        query_type="acronym",
    ),
    # 희소 복합명사: "이물질" + "5,000원" — 두 단어가 동시에 있는 청크는 1개뿐
    GoldenItem(
        query="상품에 이물질이 들어 있으면 어떻게 되나요?",
        tool="search_policy", tool_args={"policy_type": "return"},
        keywords=["이물질", "5,000원"],
        query_type="sparse_match",
    ),
    # 복합 수치 조건: "1,000포인트" — 포인트 최소 사용 기준
    GoldenItem(
        query="포인트 얼마부터 쓸 수 있어요?",
        tool="search_policy", tool_args={"policy_type": "membership"},
        keywords=["1,000포인트"],
        query_type="sparse_match",
    ),
    # 복합 수치 조건: "100%" + "황금" — 황금 회원 전용 한도, 쿼리에 수치 없음
    GoldenItem(
        query="최상위 회원이면 포인트 제한 없이 다 쓸 수 있나요?",
        tool="search_policy", tool_args={"policy_type": "membership"},
        keywords=["100%", "황금"],
        query_type="sparse_match",
    ),
    # 희소 복합명사: "웰컴 쿠폰" + "3,000원" — 신규 가입 혜택, 쿼리에 용어 없음
    GoldenItem(
        query="처음 가입하면 혜택 주나요?",
        tool="search_policy", tool_args={"policy_type": "membership"},
        keywords=["웰컴", "3,000원"],
        query_type="sparse_match",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# 검색 전략 정의
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    text: str          # 반환된 텍스트 (cs_tools 결과 혹은 docs 합산)
    latency_ms: float


def _tool_to_collections(item: GoldenItem) -> list[str]:
    """Baseline용: tool/tool_args → ChromaDB 컬렉션 목록 변환.

    Baseline은 cs_tools 없이 rag.retrieve()를 직접 호출하므로
    컬렉션 이름을 미리 결정해야 합니다.
    """
    if item.tool == "search_faq":
        return ["faq"]
    pt = item.tool_args.get("policy_type", "all")
    return POLICY_COLLECTIONS.get(pt, POLICY_COLLECTIONS["all"])


def baseline_retrieve(rag: RAGService, item: GoldenItem, top_k: int) -> SearchResult:
    """Baseline: Dense-only, 정규화·필터 없음.

    개선 전 단순 벡터 검색 행동을 재현합니다.
    여러 컬렉션이 있을 때는 각 컬렉션에서 독립 검색 후 합칩니다.
    """
    t0 = time.perf_counter()
    seen: set[str] = set()
    docs: list[str] = []
    for col in _tool_to_collections(item):
        for doc in rag.retrieve(
            item.query, col,
            top_k=top_k,
            distance_threshold=settings.rag_distance_threshold,
        ):
            if doc not in seen:
                seen.add(doc)
                docs.append(doc)
    elapsed = (time.perf_counter() - t0) * 1000
    return SearchResult(text="\n\n".join(docs[:top_k]), latency_ms=elapsed)


async def improved_retrieve(
    search_faq_fn,
    search_policy_fn,
    item: GoldenItem,
    top_k: int,
) -> SearchResult:
    """Improved: 프로덕션 cs_tool 함수를 직접 호출.

    search_faq  → normalize_query + Dense + subcategory 메타 필터
    search_policy → normalize_query + _split_query + hybrid(BM25+Dense) + Rerank
    """
    t0 = time.perf_counter()
    if item.tool == "search_faq":
        text = await search_faq_fn(query=item.query, top_k=top_k, **item.tool_args)
    else:
        text = await search_policy_fn(query=item.query, **item.tool_args)
    elapsed = (time.perf_counter() - t0) * 1000
    return SearchResult(text=text or "", latency_ms=elapsed)


# ─────────────────────────────────────────────────────────────────────────────
# 지표 계산
# ─────────────────────────────────────────────────────────────────────────────

def _split_docs(text: str) -> list[str]:
    """검색 결과 텍스트를 개별 문서 단위로 분리 (순위 계산용).

    search_policy / search_faq 모두 "\n\n" 구분자로 문서를 연결합니다.
    빈 세그먼트는 제거합니다.
    """
    return [seg.strip() for seg in text.split("\n\n") if seg.strip()]


def is_relevant(text: str, keywords: list[str]) -> bool:
    """텍스트에 키워드가 1개 이상 포함되면 relevant 판정."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def first_relevant_rank(text: str, keywords: list[str], top_k: int) -> int:
    """첫 번째 관련 문서의 순위를 반환 (1-based).

    순위 계산:
      - text를 "\n\n" 기준으로 분리 → 각 세그먼트가 하나의 청크
      - 키워드가 포함된 첫 번째 세그먼트의 순위 반환
      - 없으면 top_k + 1 반환 (miss)
    """
    segments = _split_docs(text)
    for i, seg in enumerate(segments[:top_k], start=1):
        if is_relevant(seg, keywords):
            return i
    return top_k + 1


@dataclass
class EvalResult:
    query: str
    query_type: str
    tool: str
    hit: bool        # Hit@k (k = top_k)
    hit1: bool       # Hit@1 — 정답이 rank 1에 있는지 (Reranker 정밀도 지표)
    rank: int        # 첫 번째 관련 문서 순위 (없으면 top_k+1)
    mrr: float       # 1/rank if hit else 0
    latency_ms: float
    empty: bool


def evaluate_item(result: SearchResult, item: GoldenItem, top_k: int) -> EvalResult:
    empty = not result.text or result.text.endswith("없습니다.")
    rank  = first_relevant_rank(result.text, item.keywords, top_k)
    hit   = rank <= top_k
    hit1  = rank == 1
    return EvalResult(
        query=item.query,
        query_type=item.query_type,
        tool=item.tool,
        hit=hit,
        hit1=hit1,
        rank=rank,
        mrr=1.0 / rank if hit else 0.0,
        latency_ms=result.latency_ms,
        empty=empty,
    )


@dataclass
class AggregateMetrics:
    name: str
    hit_rate: float      # Hit@k
    hit1_rate: float     # Hit@1 (Precision@1) — Reranker 효과 측정 핵심 지표
    mrr: float
    mean_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    n: int
    empty_rate: float = 0.0
    per_type: dict[str, dict] = field(default_factory=dict)
    per_tool: dict[str, dict] = field(default_factory=dict)


def aggregate(name: str, results: list[EvalResult]) -> AggregateMetrics:
    n = len(results)
    if n == 0:
        return AggregateMetrics(
            name=name, hit_rate=0, hit1_rate=0, mrr=0, mean_latency_ms=0,
            p50_latency_ms=0, p95_latency_ms=0, n=0,
        )

    hit_rate   = mean(r.hit  for r in results)
    hit1_rate  = mean(r.hit1 for r in results)
    mrr        = mean(r.mrr  for r in results)
    empty_rate = mean(r.empty for r in results)
    latencies  = [r.latency_ms for r in results]
    mean_lat   = mean(latencies)
    p50        = median(latencies)
    p95        = quantiles(latencies, n=20)[-1] if len(latencies) >= 2 else latencies[0]

    # 유형별 집계 (Hit@1 포함)
    types: dict[str, list[EvalResult]] = {}
    for r in results:
        types.setdefault(r.query_type, []).append(r)
    per_type = {
        t: {
            "hit_rate":  mean(x.hit  for x in rs),
            "hit1_rate": round(mean(x.hit1 for x in rs), 3),
            "mrr":       round(mean(x.mrr  for x in rs), 3),
            "n":         len(rs),
        }
        for t, rs in types.items()
    }

    # 도구별 집계
    tools_map: dict[str, list[EvalResult]] = {}
    for r in results:
        tools_map.setdefault(r.tool, []).append(r)
    per_tool = {
        t: {
            "hit_rate":        mean(x.hit  for x in rs),
            "hit1_rate":       round(mean(x.hit1 for x in rs), 3),
            "mrr":             round(mean(x.mrr  for x in rs), 3),
            "mean_latency_ms": round(mean(x.latency_ms for x in rs), 1),
            "n":               len(rs),
        }
        for t, rs in tools_map.items()
    }

    return AggregateMetrics(
        name=name,
        hit_rate=hit_rate,
        hit1_rate=hit1_rate,
        mrr=mrr,
        mean_latency_ms=mean_lat,
        p50_latency_ms=p50,
        p95_latency_ms=p95,
        n=n,
        empty_rate=empty_rate,
        per_type=per_type,
        per_tool=per_tool,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 청킹 품질 분석
# ─────────────────────────────────────────────────────────────────────────────

def analyze_chunk_quality(rag: RAGService) -> dict:
    """ChromaDB 컬렉션 청크 품질 지표 분석."""
    from statistics import mean as _mean

    target_collections = [
        "faq",
        "payment_policy", "delivery_policy", "return_policy",
        "quality_policy", "service_policy", "membership_policy",
    ]

    total_chunks = 0
    all_lengths: list[int] = []
    prefix_count = 0
    section_meta_count = 0
    col_stats: dict[str, int] = {}

    for col_name in target_collections:
        try:
            col = rag.chroma_client.get_collection(col_name)
            res = col.get(include=["documents", "metadatas"])
            docs  = res.get("documents", [])
            metas = res.get("metadatas", [])
            col_stats[col_name] = len(docs)
            total_chunks += len(docs)

            for doc, meta in zip(docs, metas):
                all_lengths.append(len(doc))
                if doc.strip().startswith("["):
                    prefix_count += 1
                if meta and any(k in meta for k in ("section", "article", "chapter", "doc_title")):
                    section_meta_count += 1
        except Exception:
            col_stats[col_name] = 0

    if not all_lengths:
        return {"error": "청크 없음 — seed_rag.py를 먼저 실행하세요."}

    return {
        "total_chunks": total_chunks,
        "avg_chunk_chars": round(_mean(all_lengths), 1),
        "min_chunk_chars": min(all_lengths),
        "max_chunk_chars": max(all_lengths),
        "source_prefix_rate": round(prefix_count / total_chunks, 3),
        "section_metadata_rate": round(section_meta_count / total_chunks, 3),
        "collection_counts": col_stats,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 출력 포맷터
# ─────────────────────────────────────────────────────────────────────────────

def _bar(ratio: float, width: int = 20) -> str:
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled)


def print_comparison(base: AggregateMetrics, imp: AggregateMetrics) -> None:
    delta_hit  = imp.hit_rate  - base.hit_rate
    delta_hit1 = imp.hit1_rate - base.hit1_rate
    delta_mrr  = imp.mrr       - base.mrr
    delta_lat  = imp.mean_latency_ms - base.mean_latency_ms

    print("\n" + "═" * 76)
    print(f"  RAG 성능 평가 보고서  (N={base.n}개 쿼리, 15개 = Easy 5 + Medium 5 + Hard 5)")
    print("═" * 76)

    print(f"\n  {'지표':<16}  {'Baseline (Dense-only)':<24}  {'Improved (Hybrid+Rerank)':<24}  향상")
    print(f"  {'─'*16}  {'─'*24}  {'─'*24}  {'─'*10}")

    def _row(label, bval, ival, delta):
        print(f"  {label:<16}  {bval:<24}  {ival:<24}  {delta}")

    _row(
        "Hit Rate@3",
        f"{base.hit_rate:.1%}  {_bar(base.hit_rate, 10)}",
        f"{imp.hit_rate:.1%}  {_bar(imp.hit_rate, 10)}",
        f"{delta_hit:+.1%}",
    )
    _row(
        "Hit Rate@1 ★",
        f"{base.hit1_rate:.1%}  {_bar(base.hit1_rate, 10)}",
        f"{imp.hit1_rate:.1%}  {_bar(imp.hit1_rate, 10)}",
        f"{delta_hit1:+.1%}  ← Reranker 효과",
    )
    _row(
        "MRR",
        f"{base.mrr:.3f}",
        f"{imp.mrr:.3f}",
        f"{delta_mrr:+.3f}",
    )
    _row(
        "Mean Latency",
        f"{base.mean_latency_ms:.1f} ms",
        f"{imp.mean_latency_ms:.1f} ms",
        f"{delta_lat:+.1f} ms",
    )
    _row(
        "P95 Latency",
        f"{base.p95_latency_ms:.1f} ms",
        f"{imp.p95_latency_ms:.1f} ms",
        f"{imp.p95_latency_ms - base.p95_latency_ms:+.1f} ms",
    )

    print("\n" + "─" * 76)
    print("  쿼리 난이도/유형별 Hit@1 & MRR  (★ 핵심 — rank 1 정밀도)")
    print("─" * 76)
    type_labels = {
        "direct":       "직접 질문    ",
        "colloquial":   "구어체 변형  ",
        "synonym":      "동의어 변형  ",
        "multi_intent": "복합 의도    ",
        "exact_number": "정확한 수치  ",
        "acronym":      "약어(OOV)    ",
        "sparse_match": "희소 복합명사",
    }
    print(f"  {'유형':<14}  {'B@1':>6}  {'I@1':>6}  {'@1향상':>7}  {'B MRR':>6}  {'I MRR':>6}  {'MRR향상':>7}  N")
    print(f"  {'─'*14}  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*6}  {'─'*6}  {'─'*7}  ─")
    # 난이도 순서로 정렬
    _type_order = ["colloquial", "synonym", "multi_intent", "direct",
                   "exact_number", "acronym", "sparse_match"]
    all_types = _type_order + sorted(
        t for t in set(list(base.per_type.keys()) + list(imp.per_type.keys()))
        if t not in _type_order
    )
    for t in all_types:
        if t not in base.per_type and t not in imp.per_type:
            continue
        b = base.per_type.get(t, {})
        i = imp.per_type.get(t, {})
        b_h1 = b.get("hit1_rate", 0)
        i_h1 = i.get("hit1_rate", 0)
        b_mr = b.get("mrr", 0)
        i_mr = i.get("mrr", 0)
        n    = max(b.get("n", 0), i.get("n", 0))
        label = type_labels.get(t, f"{t:<14}")
        print(
            f"  {label:<14}  {b_h1:>5.0%}  {i_h1:>5.0%}  {i_h1-b_h1:>+6.0%}  "
            f"{b_mr:>6.3f}  {i_mr:>6.3f}  {i_mr-b_mr:>+6.3f}  {n}"
        )

    print("\n" + "─" * 76)
    print("  도구별 Improved Hit@1 & Latency")
    print("─" * 76)
    tool_labels = {
        "search_faq":    "search_faq    (normalize+Dense+filter)",
        "search_policy": "search_policy (normalize+Hybrid+Rerank)",
    }
    for tool, stats in sorted(imp.per_tool.items()):
        label = tool_labels.get(tool, tool)
        print(
            f"  {label:<44}  "
            f"Hit@3={stats['hit_rate']:.0%}  Hit@1={stats['hit1_rate']:.0%}  "
            f"{stats['mean_latency_ms']:.0f}ms  (n={stats['n']})"
        )

    print("═" * 76)


def print_chunk_quality(stats: dict) -> None:
    if "error" in stats:
        print(f"\n[청킹 분석 실패] {stats['error']}")
        return

    print("\n" + "─" * 72)
    print("  청킹 품질 분석 (파싱·청킹 개선 지표)")
    print("─" * 72)
    print(f"  총 청크 수           : {stats['total_chunks']:,}")
    print(f"  평균 청크 길이       : {stats['avg_chunk_chars']} 자")
    print(f"  최소 / 최대 청크     : {stats['min_chunk_chars']} / {stats['max_chunk_chars']} 자")
    print(f"  출처 프리픽스 포함률 : {stats['source_prefix_rate']:.1%}  (LLM 인용 근거 삽입)")
    print(f"  섹션 메타 보유율     : {stats['section_metadata_rate']:.1%}  (where 필터 지원)")
    print()
    print("  컬렉션별 청크 수:")
    for col, cnt in stats["collection_counts"].items():
        if cnt > 0:
            print(f"    {col:<26} : {cnt:>4}개")
    print("─" * 72)


# ─────────────────────────────────────────────────────────────────────────────
# 메인 (async)
# ─────────────────────────────────────────────────────────────────────────────

async def _run(args):
    top_k    = args.top_k
    verbose  = args.verbose
    use_rerank = not args.no_rerank

    print(f"\n[RAG 평가 시작]  embed={settings.embed_provider}/{settings.embed_model}")
    print(f"  top_k={top_k}  rerank={'ON' if use_rerank else 'OFF'}")
    print(f"  골든셋: {len(GOLDEN_SET)}개 쿼리\n")
    print("  평가 방식: cs_tools 레벨 (Supervisor LLM 라우팅 결정을 하드코딩 시뮬레이션)")
    print("  Baseline = rag.retrieve() (pure Dense, normalize 없음)")
    print("  Improved = search_faq / search_policy (프로덕션 코드 그대로)\n")

    # ── RAGService 초기화 ────────────────────────────────────────────────────
    rag = RAGService()
    if rag.chroma_client is None:
        print("[오류] ChromaDB 초기화 실패. seed_rag.py를 먼저 실행하세요.")
        sys.exit(1)

    # ── cs_tools 빌드 (db=None, user_id=None — RAG 도구만 사용) ──────────────
    # reranking 제어: no_rerank 플래그 적용을 위해 settings 임시 오버라이드
    original_reranker = settings.reranker_model
    if not use_rerank:
        settings.reranker_model = ""

    tools, _ctx = build_cs_tools(rag_service=rag, db=None, user_id=None)
    search_faq_fn    = next(t.coroutine for t in tools if t.name == "search_faq")
    search_policy_fn = next(t.coroutine for t in tools if t.name == "search_policy")

    # ── 청킹 품질 분석 ──────────────────────────────────────────────────────
    chunk_stats = analyze_chunk_quality(rag)
    print_chunk_quality(chunk_stats)

    # ── 워밍업 (cold-start 레이턴시 제거) ───────────────────────────────────
    # 골든셋에 포함된 도구 종류만 예열 — 없는 도구는 건너뜀
    print("\n[워밍업 중... 임베딩·리랭커 모델 로딩]", flush=True)
    _faq_items    = [it for it in GOLDEN_SET if it.tool == "search_faq"]
    _policy_items = [it for it in GOLDEN_SET if it.tool == "search_policy"]
    for _ in range(2):
        if _faq_items:
            baseline_retrieve(rag, _faq_items[0], top_k)
            await improved_retrieve(search_faq_fn, search_policy_fn, _faq_items[0], top_k)
        if _policy_items:
            baseline_retrieve(rag, _policy_items[0], top_k)
            await improved_retrieve(search_faq_fn, search_policy_fn, _policy_items[0], top_k)
    _warmed = " + ".join(filter(None, [
        "FAQ" if _faq_items else "", "Policy" if _policy_items else ""
    ]))
    print(f"  워밍업 완료 ({_warmed} 예열)\n")

    # ── 검색 평가 ───────────────────────────────────────────────────────────
    base_results: list[EvalResult] = []
    imp_results:  list[EvalResult] = []

    print("[검색 평가 진행 중...]")
    for i, item in enumerate(GOLDEN_SET, 1):
        b_sr = baseline_retrieve(rag, item, top_k)
        b_ev = evaluate_item(b_sr, item, top_k)
        base_results.append(b_ev)

        i_sr = await improved_retrieve(search_faq_fn, search_policy_fn, item, top_k)
        i_ev = evaluate_item(i_sr, item, top_k)
        imp_results.append(i_ev)

        if verbose:
            b_mark = "✓" if b_ev.hit else "✗"
            i_mark = "✓" if i_ev.hit else "✗"
            b1_mark = "①" if b_ev.hit1 else "·"
            i1_mark = "①" if i_ev.hit1 else "·"
            tool_short = "FAQ   " if item.tool == "search_faq" else "Policy"
            print(
                f"  [{i:02d}] B:{b_mark}{b1_mark} I:{i_mark}{i1_mark}  {tool_short}  "
                f"{item.query_type:<14}  {item.query[:42]}"
            )
        else:
            print(f"  [{i:02d}/{len(GOLDEN_SET)}] {'✓' if i_ev.hit else '✗'} {item.query[:55]}", end="\r")

    print(" " * 72, end="\r")

    # 원복
    settings.reranker_model = original_reranker

    # ── 집계 및 출력 ────────────────────────────────────────────────────────
    base_agg = aggregate("Baseline", base_results)
    imp_agg  = aggregate("Improved", imp_results)
    print_comparison(base_agg, imp_agg)

    # ── 미스 케이스 ─────────────────────────────────────────────────────────
    failed = [r for r in imp_results if not r.hit]
    if failed:
        print(f"\n[미스 케이스] Improved에서 Hit 실패한 쿼리 ({len(failed)}개):")
        for r in failed:
            print(f"  - [{r.query_type}] {r.query}")
    else:
        print("\n✅  Improved 방식에서 모든 쿼리 Hit 성공!")

    # ── JSON 내보내기 ────────────────────────────────────────────────────────
    if args.export:
        export_data = {
            "config": {
                "top_k": top_k,
                "use_rerank": use_rerank,
                "embed_provider": settings.embed_provider,
                "embed_model": settings.embed_model,
                "reranker_model": original_reranker if use_rerank else None,
                "golden_set_size": len(GOLDEN_SET),
                "eval_level": "cs_tools (post-LLM-routing simulation)",
            },
            "chunk_quality": chunk_stats,
            "baseline": {
                "hit_rate":        round(base_agg.hit_rate, 4),
                "hit1_rate":       round(base_agg.hit1_rate, 4),
                "mrr":             round(base_agg.mrr, 4),
                "mean_latency_ms": round(base_agg.mean_latency_ms, 2),
                "p95_latency_ms":  round(base_agg.p95_latency_ms, 2),
                "per_type":        base_agg.per_type,
                "per_tool":        base_agg.per_tool,
            },
            "improved": {
                "hit_rate":        round(imp_agg.hit_rate, 4),
                "hit1_rate":       round(imp_agg.hit1_rate, 4),
                "mrr":             round(imp_agg.mrr, 4),
                "mean_latency_ms": round(imp_agg.mean_latency_ms, 2),
                "p95_latency_ms":  round(imp_agg.p95_latency_ms, 2),
                "per_type":        imp_agg.per_type,
                "per_tool":        imp_agg.per_tool,
            },
            "delta": {
                "hit_rate":        round(imp_agg.hit_rate  - base_agg.hit_rate,  4),
                "hit1_rate":       round(imp_agg.hit1_rate - base_agg.hit1_rate, 4),
                "mrr":             round(imp_agg.mrr       - base_agg.mrr,       4),
                "mean_latency_ms": round(imp_agg.mean_latency_ms - base_agg.mean_latency_ms, 2),
            },
            "per_query_baseline": [asdict(r) for r in base_results],
            "per_query_improved": [asdict(r) for r in imp_results],
        }
        with open(args.export, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        print(f"\n📄 결과 저장: {args.export}")

    return base_agg, imp_agg


def main():
    parser = argparse.ArgumentParser(description="RAG 파이프라인 성능 평가 (cs_tools 레벨)")
    parser.add_argument("--top-k",    type=int, default=3,  help="검색 결과 개수 (기본 3)")
    parser.add_argument("--no-rerank", action="store_true", help="재랭킹 비활성화")
    parser.add_argument("--export",   type=str, default="", help="결과를 JSON 파일로 저장")
    parser.add_argument("--verbose",  action="store_true",  help="쿼리별 상세 결과 출력")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
