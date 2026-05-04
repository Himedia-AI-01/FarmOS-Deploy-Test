"""FAQ 작성 에이전트 LangChain StructuredTool 정의.

각 도구는 런타임 의존성(rag_service, db)을 클로저로 캡처합니다.
build_faq_writer_tools(rag, db) 팩토리로 호출마다 생성합니다.
"""
from __future__ import annotations

import json as _json
import logging
import re as _re
from typing import TYPE_CHECKING, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from ai.rag import RAGService

logger = logging.getLogger(__name__)

# ── Citation 정규화 ───────────────────────────────────────────────────────────

_JO_RE = _re.compile(r'제\d+조(?:\([^)]*\))?')   # 제N조 / 제N조(조항명)
_HANG_RE = _re.compile(r'제\d+항')                # 제N항


def _normalize_citation(meta: dict, doc_text: str) -> dict:
    """ChromaDB 메타데이터와 문서 텍스트에서 citation 필드를 정규화합니다.

    seed_rag.py chunk_by_articles()가 저장하는 실제 메타데이터 키를 사용합니다:
        doc_title → 정책 문서명
        article   → "제N조(조항명)" 형식
        chapter   → "제N장 장제목" 형식 (없으면 키 자체 없음)

    반환:
        {"doc": "정책문서명", "chapter": "제N장 장제목 또는 ''", "article": "제N조(조항명)", "clause": "제N항 또는 null"}
    """
    doc = meta.get("doc_title", "") or meta.get("citation_doc", "")
    raw_article = meta.get("article", "") or meta.get("citation_article", "") or ""
    chapter = meta.get("chapter", "") or ""

    # 조(條) 추출: article 메타데이터에서 탐색, 없으면 doc_text 앞부분에서 추출
    article = ""
    m = _JO_RE.search(raw_article)
    if m:
        article = m.group()
    if not article:
        m = _JO_RE.search(doc_text[:300])
        if m:
            article = m.group()

    # 항(項) 추출: 문서 본문 앞 500자에서 탐색
    clause = None
    m = _HANG_RE.search(doc_text[:500])
    if m:
        clause = m.group()

    return {"doc": doc, "chapter": chapter, "article": article, "clause": clause}


# policy_type → ChromaDB 컬렉션명 (cs_tools.py POLICY_COLLECTIONS와 동일)
_POLICY_COLLECTIONS: dict[str, list[str]] = {
    "return":     ["return_policy"],
    "payment":    ["payment_policy"],
    "membership": ["membership_policy"],
    "delivery":   ["delivery_policy"],
    "quality":    ["quality_policy"],
    "service":    ["service_policy"],
    "all": [
        "return_policy", "payment_policy", "membership_policy",
        "delivery_policy", "quality_policy", "service_policy",
    ],
}


# ── 입력 스키마 ───────────────────────────────────────────────────────────────

class SearchFaqContextInput(BaseModel):
    query: str = Field(description="유사 FAQ를 검색할 쿼리")
    top_k: int = Field(default=3, ge=1, le=5, description="반환할 FAQ 수 (기본 3)")


class SearchPolicyInput(BaseModel):
    query: str = Field(description="정책 검색 쿼리")
    policy_type: Literal[
        "return", "payment", "membership", "delivery", "quality", "service", "all"
    ] = Field(
        description=(
            "조회할 정책 유형: "
            "return(반품·교환), payment(결제), membership(멤버십), "
            "delivery(배송), quality(품질), service(서비스), all(전체)"
        )
    )
    top_k: int = Field(default=2, ge=1, le=4, description="반환할 정책 청크 수 (기본 2)")


class GetFaqCategoriesInput(BaseModel):
    pass


# ── 도구 팩토리 ───────────────────────────────────────────────────────────────

def build_faq_writer_tools(
    rag_service: "RAGService",
    db: "Session",
) -> tuple[list[StructuredTool], dict[str, StructuredTool]]:
    """FAQ 작성 에이전트용 도구 목록을 생성합니다.

    rag_service와 db를 클로저로 캡처하여 각 도구에 바인딩합니다.

    Returns:
        (tools, tool_map) — 도구 목록과 이름→도구 매핑 딕셔너리
    """

    def search_faq_context(query: str, top_k: int = 3) -> str:
        """유사 기존 FAQ를 검색하여 '질문 | 답변' 형식으로 반환합니다.
        작성할 FAQ의 문체·수준·형식을 기존 FAQ와 일치시키는 데 사용하세요.
        """
        try:
            results = rag_service.hybrid_retrieve(
                query, collections=["faq"], top_k=top_k
            )
            if not results:
                return "(유사 FAQ 없음)"

            lines = []
            for i, doc in enumerate(results, 1):
                # ChromaDB 포맷: "[카테고리] Q: 질문\nA: 답변"
                # 카테고리 prefix 제거 후 Q / A 분리
                stripped = _re.sub(r"^\[.*?\]\s*", "", doc).strip()
                parts = stripped.split("\nA:", 1)
                if len(parts) == 2:
                    q_part = _re.sub(r"^Q:\s*", "", parts[0]).strip()
                    a_part = parts[1].strip()
                    lines.append(f"{i}. 질문: {q_part}\n   답변: {a_part}")
                else:
                    lines.append(f"{i}. {stripped[:120]}")

            return "\n".join(lines)
        except Exception as e:
            logger.warning("[faq_writer] FAQ 컨텍스트 검색 실패 (무시): %s", e)
            return "(FAQ 검색 불가)"

    def search_policy(
        query: str,
        policy_type: str = "all",
        top_k: int = 2,
    ) -> str:
        """정책 문서를 검색하여 관련 내용과 인용 정보를 반환합니다.
        배송·환불·결제·멤버십·품질·서비스 정책을 근거로 답변할 때 사용하세요.
        반환 형식: '인용출처 JSON + 내용'
        """
        collections = _POLICY_COLLECTIONS.get(policy_type, _POLICY_COLLECTIONS["all"])
        lines: list[str] = []

        try:
            for col in collections:
                results = rag_service.retrieve_with_metadata(
                    query, col, top_k=top_k, distance_threshold=0.6
                )
                for doc_text, meta in results:
                    citation = _normalize_citation(meta, doc_text)
                    citation_json = _json.dumps(citation, ensure_ascii=False)
                    lines.append(
                        f"[인용출처]: {citation_json}\n"
                        f"[내용]:\n{doc_text[:300]}"
                    )
        except Exception as e:
            logger.warning("[faq_writer] 정책 검색 실패 (무시): %s", e)

        return "\n\n".join(lines) if lines else "(관련 정책 없음)"

    def get_faq_categories() -> str:
        """활성 FAQ 카테고리 목록을 'slug: 이름' 형식으로 반환합니다.
        FAQ의 suggested_category_slug 선택에 사용하세요.
        """
        try:
            from app.models.faq_category import FaqCategory

            cats = (
                db.query(FaqCategory.slug, FaqCategory.name)
                .filter(FaqCategory.is_active.is_(True))
                .order_by(FaqCategory.sort_order)
                .all()
            )
            if not cats:
                return "(카테고리 없음)"
            return "\n".join(f"- {slug}: {name}" for slug, name in cats)
        except Exception as e:
            logger.warning("[faq_writer] 카테고리 조회 실패 (무시): %s", e)
            return "(카테고리 조회 불가)"

    tools = [
        StructuredTool.from_function(
            func=search_faq_context,
            name="search_faq_context",
            description=(
                "유사 기존 FAQ를 검색합니다. "
                "문체·형식 참고 및 중복 FAQ 등록 방지에 활용하세요."
            ),
            args_schema=SearchFaqContextInput,
        ),
        StructuredTool.from_function(
            func=search_policy,
            name="search_policy",
            description=(
                "정책 문서를 검색합니다. "
                "배송, 환불, 결제, 멤버십, 품질, 서비스 정책 관련 질문에 사용하세요. "
                "citation JSON을 작성할 때 반환된 '[정책문서명 > 조항]' 정보를 활용하세요."
            ),
            args_schema=SearchPolicyInput,
        ),
        StructuredTool.from_function(
            func=get_faq_categories,
            name="get_faq_categories",
            description="현재 활성 FAQ 카테고리 목록을 조회합니다.",
            args_schema=GetFaqCategoriesInput,
        ),
    ]

    tool_map = {t.name: t for t in tools}
    return tools, tool_map
