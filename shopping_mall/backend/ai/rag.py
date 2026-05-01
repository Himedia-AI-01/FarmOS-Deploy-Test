"""RAG (Retrieval-Augmented Generation) service using ChromaDB."""
import logging
import os
import re
import sys
from typing import Optional

import json as _json

# Windows에서 torch OpenMP DLL 중복 로딩으로 인한 세그폴트 방지 (Windows 전용).
# bge-m3(임베딩)와 CrossEncoder(리랭커)가 각각 torch를 로딩할 때 충돌이 발생하므로
# 프로세스 시작 시점에 미리 설정해야 효과가 있다.
if sys.platform.startswith("win"):
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from app.core.config import settings
from app.paths import CHROMA_DB_PATH, AI_DATA_DIR
from ai.embeddings import get_embedding_function
from ai.utils import tokenize_ko

logger = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────────────────────────────────
# BM25/Dense 각각 top_k의 2배 후보를 뽑아 RRF 통합 후 상위 top_k 반환
_RRF_CANDIDATE_MULTIPLIER = 2

# ── BM25 지연 로딩 ─────────────────────────────────────────────────────────────

_BM25_INDEX_PATH = str(AI_DATA_DIR / "bm25_index.json")
_bm25_obj = None                   # BM25Okapi 인스턴스 (최초 호출 시 로드)
_bm25_meta: dict | None = None     # {"ids": [...], "collections": [...]}
_id_to_col: dict[str, str] = {}    # doc_id → collection 역매핑 (O(1) 조회)


def _load_bm25():
    """BM25 인덱스를 JSON에서 지연 로딩 (최초 호출 시 1회).

    seed_rag.py 실행 후 생성된 bm25_index.json을 읽어 BM25Okapi 인스턴스를 만든다.
    파일 없거나 rank_bm25 미설치 시 (None, None) 반환 → graceful degradation.
    """
    global _bm25_obj, _bm25_meta, _id_to_col
    if _bm25_obj is not None:
        return _bm25_obj, _bm25_meta
    try:
        from rank_bm25 import BM25Okapi
        with open(_BM25_INDEX_PATH, encoding="utf-8") as f:
            data = _json.load(f)
        _bm25_obj = BM25Okapi(data["corpus"])
        _bm25_meta = {"ids": data["ids"], "collections": data["collections"]}
        _id_to_col = dict(zip(data["ids"], data["collections"]))
        logger.info("BM25 인덱스 로드: %d개 문서", len(data["ids"]))
        return _bm25_obj, _bm25_meta
    except FileNotFoundError:
        logger.warning("BM25 인덱스 없음 (%s). seed_rag.py를 먼저 실행하세요.", _BM25_INDEX_PATH)
        return None, None
    except Exception as e:
        logger.warning("BM25 로드 실패: %s", e)
        return None, None


# ── 쿼리 정규화 ────────────────────────────────────────────────────────────────

# e커머스 도메인 동의어 맵: 구어체/약칭 → 표준 표현 (단방향 치환)
_SYNONYM_MAP: dict[str, str] = {
    "반품": "반품 환불",
    "환불": "반품 환불",
    "돌려보내": "반품 환불",
    "배달": "배송",
    "택배": "배송",
    "바꿔": "교환",
    "교체": "교환",
    "포인트": "적립금",
    "마일리지": "적립금",
    "캔슬": "취소",
    # "며칠" / "얼마나" 는 의문부사 — 동의어가 아니므로 제거.
    # bge-m3 시맨틱 임베딩이 자연스럽게 처리하며, 치환 시 문장 의미가 왜곡됨.
    # 예) "배송까지 얼마나 걸리나요?" → "배송까지 기간 걸리나요?" (비문)
}

# 구어체 어미 제거 (문장 끝 패턴)
_COLLOQUIAL_ENDINGS: re.Pattern[str] = re.compile(
    r"(인가요|나요|요\?|죠\?|까요|이에요|예요|어요|아요|해요|해줘|줘요|줘\??|해줘요)\s*$"
)


def normalize_query(query: str) -> str:
    """e커머스 도메인 쿼리 전처리.

    1. 구어체 어미 제거
    2. 동의어 → 표준 표현 치환 (단어 경계 기준, 한국어 lookaround)
    3. 연속 공백 정리

    원본 의미 보존 우선 — 변환 결과가 빈 문자열이면 원본 반환.

    Lookahead 설계:
      기존 (?![가-힣a-zA-Z]) 는 '캔슬하고' 처럼 외래어 동사형(하+어미)이 붙으면 매칭 실패.
      개선: 뒤가 비 한글/알파 OR '하'+한글(동사형 접미사) 일 때도 치환하도록 양성 lookahead 사용.
      예) '캔슬하고 싶은데' → 취소하고 싶은데  /  '캔슬' → 취소
    """
    q = _COLLOQUIAL_ENDINGS.sub("", query.strip()).strip()
    for src, dst in _SYNONYM_MAP.items():
        # 앞: 한글/알파 아님 (단어 경계)
        # 뒤: 비한글/비알파 OR 동사 접미사 '하'+한글 OR 문자열 끝
        pattern = (
            f"(?<![가-힣a-zA-Z]){re.escape(src)}"
            f"(?=[^가-힣a-zA-Z]|하[가-힣]|$)"
        )
        q = re.sub(pattern, dst, q)
    q = re.sub(r"\s{2,}", " ", q).strip()
    return q or query


# ── 멀티쿼리 분리 ──────────────────────────────────────────────────────────────

# 복합 의도 분리를 위한 접속사 패턴
_SPLIT_CONJUNCTIONS: re.Pattern[str] = re.compile(
    r"(?:,\s*|,?\s*(?:그리고|또한|또|및|와|이랑|하고|이고|고\s))"
)

# 의문사 목록 — 한 문장 내 반복 감지용
_QUESTION_WORDS: frozenset[str] = frozenset({"어떻게", "얼마나", "언제", "얼마", "왜", "어디", "몇"})


def _split_query(query: str) -> list[str]:
    """복합 의도 질문을 단일 의도 서브쿼리 목록으로 분리 (규칙 기반, LLM 호출 없음).

    분리 기준:
    1. 접속사(그리고/또한/및/와/이고 등) 기준 분리
    2. 의문사가 한 파트에 2개 이상이면 두 번째 의문사 직전에서 추가 분리

    Args:
        query: 사용자 질문 (normalize_query 미적용 상태도 허용)

    Returns:
        분리된 쿼리 목록. 분리 불가능하면 [normalize_query(query)].
    """
    nq = normalize_query(query)
    parts = [p.strip() for p in _SPLIT_CONJUNCTIONS.split(nq) if len(p.strip()) >= 3]

    refined: list[str] = []
    for part in parts:
        found = [w for w in _QUESTION_WORDS if w in part]
        if len(found) >= 2:
            idx = part.find(found[1])
            if idx > 5:
                refined.append(part[:idx].strip())
                refined.append(part[idx:].strip())
                continue
        refined.append(part)

    result = [p for p in refined if len(p) >= 3]
    return result if result else [nq]


# ── 재랭킹 (Cross-Encoder) ────────────────────────────────────────────────────

_reranker_obj = None         # CrossEncoder 인스턴스 캐시
_reranker_name: str = ""     # 현재 로드된 모델명


def _load_reranker(model_name: str):
    """Cross-Encoder 재랭킹 모델을 지연 로딩 (모델명이 바뀌면 재로드).

    sentence-transformers가 설치되어 있지 않거나 모델 다운로드 실패 시
    None 반환 → rerank()가 원본 순서 그대로 반환 (graceful degradation).
    """
    global _reranker_obj, _reranker_name
    if _reranker_obj is not None and _reranker_name == model_name:
        return _reranker_obj
    try:
        from sentence_transformers import CrossEncoder
        import torch
        # CPU를 명시적으로 지정 — GPU 없는 환경에서 CUDA 초기화 시도 방지.
        # KMP_DUPLICATE_LIB_OK=TRUE (모듈 최상단 설정)가 OpenMP 충돌을 해결하므로
        # torch.set_num_threads() 제한은 불필요. 기본 스레드 수를 유지해 추론 성능 확보.
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Reranker 로드 중: %s (device=%s)", model_name, device)
        _reranker_obj = CrossEncoder(model_name, device=device)
        _reranker_name = model_name
        logger.info("Reranker 로드 완료: %s", model_name)
        return _reranker_obj
    except Exception as e:
        logger.warning("Reranker 로드 실패 (%s): %s — 재랭킹 비활성화", model_name, e)
        return None


def rerank(
    query: str,
    docs: list[str],
    top_k: int = 3,
    model_name: str = "",
) -> list[str]:
    """Cross-Encoder로 후보 문서를 재랭킹하여 관련성 높은 순서로 반환.

    모델을 지정하지 않으면 settings.reranker_model을 사용한다.
    모델 로드 실패 또는 docs가 비어 있으면 원본 docs[:top_k]를 반환.

    Args:
        query: 사용자 원본 질문 (normalize_query 적용 전 버전 권장)
        docs: hybrid_retrieve() 등에서 수집한 후보 문서 리스트
        top_k: 최종 반환 문서 수
        model_name: CrossEncoder 모델명. 비워두면 settings.reranker_model 사용.

    Returns:
        재랭킹된 상위 top_k 문서 리스트.
    """
    if not docs:
        return []

    name = model_name or settings.reranker_model
    if not name:
        return docs[:top_k]

    reranker = _load_reranker(name)
    if reranker is None:
        return docs[:top_k]

    pairs = [(query, doc) for doc in docs]
    scores: list[float] = reranker.predict(pairs).tolist()
    ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
    return [doc for doc, _ in ranked[:top_k]]


class RAGService:
    """RAG service with ChromaDB for document retrieval and LLM for generation."""

    def __init__(self, persist_directory: str | None = None):
        self.chroma_client = None
        self._ef = None
        if persist_directory is None:
            persist_directory = CHROMA_DB_PATH
        self._init_chroma(persist_directory)

    def _init_chroma(self, persist_directory: str):
        """Initialize ChromaDB client. Gracefully handles unavailability."""
        try:
            import chromadb
            self.chroma_client = chromadb.PersistentClient(path=persist_directory)
            self._ef = get_embedding_function()
            logger.info("ChromaDB initialized (provider=%s).", settings.embed_provider)
        except Exception as e:
            logger.warning(f"ChromaDB initialization failed: {e}. RAG will use fallback.")
            self.chroma_client = None

    def _get_collection(self, collection_name: str):
        """Get a ChromaDB collection with embedding function."""
        if self.chroma_client is None:
            return None
        try:
            return self.chroma_client.get_collection(
                name=collection_name,
                embedding_function=self._ef,
            )
        except Exception as e:
            logger.error(
                f"Failed to get collection '{collection_name}': {e}. "
                "seed_rag.py를 먼저 실행하세요: uv run python ai/seed_rag.py"
            )
            return None

    def retrieve(
        self,
        question: str,
        collection: str,
        top_k: int = 3,
        distance_threshold: float = 0.5,
        where: dict | None = None,
    ) -> list[str]:
        """ChromaDB에서 관련 문서를 검색하고 거리 필터링 후 반환.

        Args:
            question: 검색 질문
            collection: 컬렉션 이름
            top_k: 최대 반환 수
            distance_threshold: 이 값 미만의 거리(유사도)인 문서만 반환
            where: 메타데이터 필터 (선택)

        Returns:
            필터링된 문서 텍스트 리스트. 관련 문서 없으면 빈 리스트.
        """
        col = self._get_collection(collection)
        if col is None:
            return []

        try:
            kwargs: dict = {
                "query_texts": [question],
                "n_results": top_k,
                "include": ["documents", "distances"],
            }
            if where:
                kwargs["where"] = where

            results = col.query(**kwargs)
            documents = results.get("documents", [[]])[0]
            distances = results.get("distances", [[]])[0]

            filtered = [
                doc for doc, dist in zip(documents, distances)
                if dist < distance_threshold
            ]
            return filtered

        except Exception as e:
            logger.warning(f"RAG retrieve failed (collection={collection}): {e}")
            return []

    def retrieve_with_scores(
        self,
        question: str,
        collection: str,
        top_k: int = 3,
        distance_threshold: float = 0.5,
        where: dict | None = None,
    ) -> list[tuple[str, float]]:
        """ChromaDB에서 관련 문서를 검색하고 (텍스트, 거리) 튜플 리스트로 반환.

        retrieve()와 동일한 필터링 기준을 적용하되 거리값을 함께 반환하여
        후처리(정렬, RRF 계산 등)를 가능하게 한다.

        Returns:
            [(doc_text, distance), ...] — distance 오름차순 정렬.
            관련 문서 없으면 빈 리스트.
        """
        col = self._get_collection(collection)
        if col is None:
            return []

        try:
            kwargs: dict = {
                "query_texts": [question],
                "n_results": top_k,
                "include": ["documents", "distances"],
            }
            if where:
                kwargs["where"] = where

            results = col.query(**kwargs)
            documents = results.get("documents", [[]])[0]
            distances = results.get("distances", [[]])[0]

            filtered = [
                (doc, dist)
                for doc, dist in zip(documents, distances)
                if dist < distance_threshold
            ]
            filtered.sort(key=lambda x: x[1])
            return filtered

        except Exception as e:
            logger.warning(f"RAG retrieve_with_scores failed (collection={collection}): {e}")
            return []

    def retrieve_with_metadata(
        self,
        question: str,
        collection: str,
        top_k: int = 3,
        distance_threshold: float = 0.5,
        where: dict | None = None,
    ) -> list[tuple[str, dict]]:
        """ChromaDB에서 관련 문서를 검색하고 (텍스트, 메타데이터) 튜플 리스트로 반환.

        FAQ 인용 추적에 필요한 db_id 등 메타데이터를 함께 반환합니다.

        Args:
            question: 검색 질문
            collection: 컬렉션 이름
            top_k: 최대 반환 수
            distance_threshold: 이 값 미만의 거리(유사도)인 문서만 반환
            where: 메타데이터 필터 (선택)

        Returns:
            [(doc_text, metadata_dict), ...] — distance 오름차순 정렬.
            관련 문서 없으면 빈 리스트.
        """
        col = self._get_collection(collection)
        if col is None:
            return []

        try:
            kwargs: dict = {
                "query_texts": [question],
                "n_results": top_k,
                "include": ["documents", "distances", "metadatas"],
            }
            if where:
                kwargs["where"] = where

            results = col.query(**kwargs)
            documents = results.get("documents", [[]])[0]
            distances = results.get("distances", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]

            filtered = sorted(
                [
                    (doc, meta or {}, dist)
                    for doc, dist, meta in zip(documents, distances, metadatas)
                    if dist < distance_threshold
                ],
                key=lambda x: x[2],
            )
            return [(doc, meta) for doc, meta, _ in filtered]

        except Exception as e:
            logger.warning(f"RAG retrieve_with_metadata failed (collection={collection}): {e}")
            return []

    def retrieve_multiple(
        self,
        question: str,
        collections: list[str],
        top_k_per: int = 2,
        distance_threshold: float = 0.5,
    ) -> list[str]:
        """여러 컬렉션에서 검색하여 거리 기반으로 정렬한 결과 반환.

        Args:
            question: 검색 질문
            collections: 검색할 컬렉션 이름 목록
            top_k_per: 컬렉션당 최대 반환 수
            distance_threshold: 거리 필터

        Returns:
            모든 컬렉션에서 수집된 관련 문서 리스트 (중복 제거, 거리 오름차순 정렬)
        """
        seen: set[str] = set()
        scored: list[tuple[str, float]] = []
        for col_name in collections:
            for doc, dist in self.retrieve_with_scores(question, col_name, top_k_per, distance_threshold):
                if doc not in seen:
                    seen.add(doc)
                    scored.append((doc, dist))
        scored.sort(key=lambda x: x[1])
        return [doc for doc, _ in scored]

    def hybrid_retrieve(
        self,
        question: str,
        collections: list[str],
        top_k: int = 5,
        distance_threshold: float = 0.5,
        rrf_k: int = 60,
    ) -> list[str]:
        """Dense (ChromaDB) + Sparse (BM25) 하이브리드 검색 with RRF 합산.

        1. Dense: retrieve_with_scores()로 각 컬렉션 검색
        2. Sparse: BM25 인덱스에서 대상 컬렉션 필터링 후 Top-K
        3. RRF: 두 랭킹의 역순위(1/(k+rank)) 합산 → 최종 정렬

        BM25 인덱스가 없으면 Dense 결과만 반환 (graceful degradation).

        Args:
            question: 검색 질문 (normalize_query 적용 권장)
            collections: 검색 대상 컬렉션 목록
            top_k: 최종 반환 문서 수
            distance_threshold: Dense 검색 거리 필터
            rrf_k: RRF 하이퍼파라미터 (기본값 60 — 표준)
        """
        col_set = set(collections)

        # ── Dense 검색 ────────────────────────────────────────────────────────
        dense: list[tuple[str, float]] = []
        seen: set[str] = set()
        for col_name in collections:
            for doc, dist in self.retrieve_with_scores(
                question, col_name, top_k * _RRF_CANDIDATE_MULTIPLIER, distance_threshold
            ):
                if doc not in seen:
                    seen.add(doc)
                    dense.append((doc, dist))
        dense.sort(key=lambda x: x[1])

        # ── BM25 검색 ────────────────────────────────────────────────────────
        bm25_obj, bm25_meta = _load_bm25()
        if bm25_obj is None:
            return [doc for doc, _ in dense[:top_k]]

        tokens = tokenize_ko(question)
        if not tokens:
            return [doc for doc, _ in dense[:top_k]]

        all_scores = bm25_obj.get_scores(tokens)
        # 대상 컬렉션 소속 문서만 필터링 후 내림차순 정렬
        valid = [
            (i, score)
            for i, (score, col) in enumerate(zip(all_scores, bm25_meta["collections"]))
            if col in col_set
        ]
        valid.sort(key=lambda x: x[1], reverse=True)
        top_bm25_ids = [bm25_meta["ids"][i] for i, _ in valid[:top_k * _RRF_CANDIDATE_MULTIPLIER]]

        # ChromaDB에서 BM25 상위 문서 텍스트 조회
        bm25_docs: list[str] = []
        for col_name in collections:
            col_obj = self._get_collection(col_name)
            if col_obj is None:
                continue
            col_ids = [
                doc_id for doc_id in top_bm25_ids
                if _id_to_col.get(doc_id) == col_name
            ]
            if col_ids:
                try:
                    res = col_obj.get(ids=col_ids, include=["documents"])
                    # col.get()은 내부 순서를 보장하지 않으므로 top_bm25_ids 기준으로 재정렬
                    id_to_doc = dict(zip(res.get("ids", []), res.get("documents", [])))
                    bm25_docs.extend(
                        id_to_doc[doc_id]
                        for doc_id in col_ids
                        if doc_id in id_to_doc
                    )
                except Exception as e:
                    logger.warning("BM25 문서 조회 실패 (%s): %s", col_name, e)

        # ── RRF 합산 ─────────────────────────────────────────────────────────
        rrf: dict[str, float] = {}
        for rank, (doc, _) in enumerate(dense):
            rrf[doc] = rrf.get(doc, 0.0) + 1.0 / (rrf_k + rank)
        for rank, doc in enumerate(bm25_docs):
            rrf[doc] = rrf.get(doc, 0.0) + 1.0 / (rrf_k + rank)

        return sorted(rrf, key=lambda d: rrf[d], reverse=True)[:top_k]

    def add_documents(self, collection: str, docs: list[dict]) -> int:
        """Add documents to a ChromaDB collection.
        Each doc should have 'id', 'text', and optional 'metadata'.
        Returns count of added documents.
        """
        col = self._get_collection(collection)
        if col is None:
            logger.warning("Cannot add documents: ChromaDB not available.")
            return 0

        try:
            ids = [d["id"] for d in docs]
            texts = [d["text"] for d in docs]
            metadatas = [d.get("metadata", {}) for d in docs]
            col.add(documents=texts, ids=ids, metadatas=metadatas)
            return len(docs)
        except Exception as e:
            logger.warning(f"Failed to add documents: {e}")
            return 0
