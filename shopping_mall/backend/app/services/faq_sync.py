"""⑤ CS 지식베이스 자동 동기화 서비스.

PostgreSQL(FaqDoc) → ChromaDB 단방향 동기화를 담당합니다.
모든 public 메서드는 staticmethod (stateless).

사용 방법:
    # FastAPI BackgroundTask로 비동기 처리 (사용자 응답에 영향 없음)
    background_tasks.add_task(FaqSync.upsert, doc)
    background_tasks.add_task(FaqSync.delete, doc.chroma_doc_id, doc.chroma_collection)

설계:
    - ChromaDB 쓰기는 동기 I/O → BackgroundTask에서 실행 (이벤트 루프 블로킹 없음)
    - BM25 인덱스는 ChromaDB 전체 문서 기반 재빌드 → 같은 BackgroundTask 체인 끝에 실행
    - 임베딩 함수는 RAGService와 동일한 설정을 사용 (provider 일관성 보장)
    - BM25 재빌드는 디바운스됨 (30초 간격) — 대량 upsert 시 성능 최적화
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time

import chromadb.errors
from typing import TYPE_CHECKING

from app.paths import CHROMA_DB_PATH, AI_DATA_DIR

if TYPE_CHECKING:
    from app.models.faq_doc import FaqDoc

# 모듈 임포트는 백엔드의 패키지 구조에 따라 결정
# ai/는 백엔드 루트의 sibling이므로 상대 임포트
try:
    from ai.utils import tokenize_ko
except ImportError:
    # 폴백: 로컬 정의
    def tokenize_ko(text: str) -> list[str]:
        """한국어 정규식 토크나이저 (로컬 폴백)."""
        tokens = re.findall(r"[가-힣a-zA-Z0-9]+", text.lower())
        return tokens if tokens else [text.lower()]

logger = logging.getLogger(__name__)

BM25_INDEX_PATH = str(AI_DATA_DIR / "bm25_index.json")

# ── BM25 재빌드 디바운스 ──────────────────────────────────────────────────────
_last_bm25_rebuild: float = 0.0
_BM25_REBUILD_DEBOUNCE_SEC: float = 30.0
_bm25_rebuild_lock: threading.Lock = threading.Lock()

# BM25 인덱스에 포함되는 컬렉션 목록 (seed_rag.py _ALL_COLLECTIONS와 동기화)
# 통합 FAQ 전환 이후: 모든 FAQ 문서는 단일 "faq" 컬렉션에 저장됨
# 구 컬렉션(storage_guide, season_info, farm_intro)은 레거시 호환용으로 유지
_ALL_COLLECTIONS = [
    "faq",             # 통합 FAQ (신규 표준)
    "storage_guide",   # 레거시 (마이그레이션 완료 후 제거 가능)
    "season_info",     # 레거시
    "farm_intro",      # 레거시
    "payment_policy",
    "delivery_policy",
    "return_policy",
    "quality_policy",
    "service_policy",
    "membership_policy",
]




def _get_chroma_client():
    """ChromaDB PersistentClient를 반환합니다."""
    import chromadb
    return chromadb.PersistentClient(path=CHROMA_DB_PATH)


def _get_embedding_function():
    """ai/embeddings.py의 임베딩 함수를 반환합니다 (RAGService와 동일 설정)."""
    from ai.embeddings import get_embedding_function
    return get_embedding_function()


class FaqSync:
    """ChromaDB 동기화 서비스 (stateless)."""

    # ── 단건 upsert ────────────────────────────────────────────────────────

    @staticmethod
    def upsert(doc: "FaqDoc") -> None:
        """FaqDoc 1건을 ChromaDB에 upsert합니다.

        BackgroundTask에서 실행되므로 예외를 삼켜 사용자 응답에 영향을 주지 않습니다.
        upsert 완료 후 BM25 인덱스를 재빌드합니다.

        Args:
            doc: upsert할 FaqDoc 인스턴스
        """
        try:
            client = _get_chroma_client()
            ef = _get_embedding_function()
            col = client.get_or_create_collection(
                name=doc.chroma_collection,
                embedding_function=ef,
            )
            col.upsert(
                ids=[doc.chroma_doc_id],
                documents=[doc.to_chroma_document()],
                metadatas=[doc.to_chroma_metadata()],
            )
            logger.info(
                "[faq_sync] upsert 완료: id=%s collection=%s",
                doc.chroma_doc_id, doc.chroma_collection,
            )
            # BM25 인덱스 재빌드 (전체 문서 기반, 30초 디바운스)
            FaqSync._rebuild_bm25_debounced(client)
        except Exception as e:
            logger.error(
                "[faq_sync] upsert 실패: id=%s collection=%s error=%s",
                getattr(doc, "chroma_doc_id", "?"),
                getattr(doc, "chroma_collection", "?"),
                e,
            )

    # ── 단건 delete ────────────────────────────────────────────────────────

    @staticmethod
    def delete(chroma_doc_id: str, chroma_collection: str) -> None:
        """ChromaDB에서 문서 1건을 삭제합니다.

        문서가 없는 경우 ChromaDB는 예외를 발생시키지 않으므로 멱등성이 보장됩니다.

        Args:
            chroma_doc_id: 삭제할 ChromaDB document ID
            chroma_collection: 대상 컬렉션명
        """
        try:
            client = _get_chroma_client()
            col = client.get_collection(name=chroma_collection)
            col.delete(ids=[chroma_doc_id])
            logger.info(
                "[faq_sync] delete 완료: id=%s collection=%s",
                chroma_doc_id, chroma_collection,
            )
            FaqSync._rebuild_bm25_debounced(client)
        except Exception as e:
            logger.error(
                "[faq_sync] delete 실패: id=%s collection=%s error=%s",
                chroma_doc_id, chroma_collection, e,
            )

    # ── BM25 인덱스 재빌드 ──────────────────────────────────────────────────

    @staticmethod
    def _rebuild_bm25_debounced(client=None) -> None:
        """30초 디바운스 — 대량 upsert 시 BM25를 매번 재빌드하지 않음.

        락 + 이중 체크(double-checked locking)로 동시 스레드가 모두 시간 체크를
        통과해 rebuild_bm25를 중복 실행하는 경쟁 조건을 방지합니다.
        """
        global _last_bm25_rebuild
        # 1차 체크: 락 획득 전 빠른 경로 (대부분의 호출이 여기서 반환)
        if time.time() - _last_bm25_rebuild < _BM25_REBUILD_DEBOUNCE_SEC:
            logger.debug("[FaqSync] BM25 재빌드 스킵 (debounce 중)")
            return
        _bm25_rebuild_lock.acquire()
        try:
            # 2차 체크: 락 획득 후 재확인 — 대기 중이던 스레드가 중복 실행하지 않도록
            if time.time() - _last_bm25_rebuild < _BM25_REBUILD_DEBOUNCE_SEC:
                logger.debug("[FaqSync] BM25 재빌드 스킵 (debounce 중, 락 획득 후 재확인)")
                return
            try:
                FaqSync.rebuild_bm25(client)
                _last_bm25_rebuild = time.time()
            except Exception as e:
                logger.error("[FaqSync] BM25 재빌드 실패 (타임스탬프 미갱신 — 다음 호출 시 재시도): %s", e)
        finally:
            _bm25_rebuild_lock.release()

    @staticmethod
    def rebuild_bm25(client=None) -> None:
        """전체 컬렉션 기반으로 BM25 인덱스를 재빌드하고 저장합니다.

        ChromaDB upsert/delete 후 자동 호출됩니다.
        파일 I/O가 실패해도 ChromaDB 동기화에는 영향이 없습니다.

        Args:
            client: 재사용할 ChromaDB 클라이언트. None이면 새로 생성합니다.
        """
        try:
            if client is None:
                client = _get_chroma_client()

            corpus: list[list[str]] = []
            ids: list[str] = []
            cols: list[str] = []

            for col_name in _ALL_COLLECTIONS:
                try:
                    col = client.get_collection(name=col_name)
                    res = col.get(include=["documents"])
                    for doc_text, doc_id in zip(
                        res.get("documents", []), res.get("ids", [])
                    ):
                        corpus.append(tokenize_ko(doc_text))
                        ids.append(doc_id)
                        cols.append(col_name)
                except chromadb.errors.NotFoundError:
                    pass  # 컬렉션 미존재 — 건너뜀
                except Exception as e:
                    logger.error("[faq_sync] BM25 재빌드 중 컬렉션 로드 실패: collection=%s error=%s", col_name, e)

            bm25_data = {"corpus": corpus, "ids": ids, "collections": cols}
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(BM25_INDEX_PATH), suffix=".tmp"
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(bm25_data, f, ensure_ascii=False)
                os.replace(tmp_path, BM25_INDEX_PATH)
            except Exception:
                os.unlink(tmp_path)
                raise

            logger.info(
                "[faq_sync] BM25 재빌드 완료: %d개 문서 → %s",
                len(ids), BM25_INDEX_PATH,
            )
        except Exception as e:
            logger.error("[faq_sync] BM25 재빌드 실패: %s", e)
