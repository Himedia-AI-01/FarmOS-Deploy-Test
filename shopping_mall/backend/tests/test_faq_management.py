"""TDD: FAQ 관리 기능 테스트

GREEN 테스트 (12개, 기존): 기존 기능 — 즉시 통과
GREEN 테스트 (13개, analytics): 신규 analytics 엔드포인트

엔드포인트 목록:
  - GET /api/admin/faq-analytics/action-summary
  - GET /api/admin/faq-analytics/unanswered-samples
  - GET /api/admin/faq-analytics/least-cited
  - GET /api/admin/faq-analytics/top-cited
  - GET /api/admin/faq-analytics/trending-questions
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# ── 테스트 DB 설정 ────────────────────────────────────────────────────────────

# StaticPool: 모든 연결이 같은 in-memory DB를 공유 — create_all 후 세션에서도 동일 테이블 접근 가능
SQLALCHEMY_TEST_URL = "sqlite:///:memory:"


@pytest.fixture
def test_engine():
    """함수 스코프 SQLite 엔진 — 각 테스트마다 새 인메모리 DB를 생성해 격리.

    모듈 스코프 엔진에 rollback 기반 격리를 사용하면 TestClient 내부의
    session.commit()이 이미 커밋된 행을 남겨 다음 테스트로 유출된다.
    함수 스코프로 전환하면 create_all / drop_all 이 매 테스트마다 실행되므로
    커밋 여부와 무관하게 완전한 격리가 보장된다.
    """
    # 필요 모델을 먼저 임포트해야 Base.metadata에 등록됨
    from app.database import Base
    import app.models  # noqa: F401

    engine = create_engine(
        SQLALCHEMY_TEST_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def db_session(test_engine):
    """각 테스트마다 독립 세션 — 테스트 엔진이 함수 스코프이므로 격리 보장."""
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = TestingSession()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def client(db_session):
    """FaqSync를 mock으로 대체한 TestClient."""
    from app.database import get_db
    from app.routers.faq import router as faq_router

    app = FastAPI()
    app.include_router(faq_router)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # ChromaDB / FaqSync 은 백그라운드 작업 — 테스트에서 차단
    with patch("app.routers.faq.FaqSync.upsert"), patch("app.routers.faq.FaqSync.delete"):
        with TestClient(app) as c:
            yield c


# ──────────────────────────────────────────────────────────────────────────────
# ✅ GREEN 테스트 (1~12): 기존 코드로 통과
# ──────────────────────────────────────────────────────────────────────────────


class TestFaqDocModel:
    """FaqDoc 모델 메서드 단위 테스트."""

    def test_to_chroma_document_with_category(self):
        """[GREEN] 카테고리 있는 문서의 ChromaDB 텍스트 포맷 검증."""
        from app.models.faq_doc import FaqDoc
        from app.models.faq_category import FaqCategory

        cat = FaqCategory(id=1, name="배송", slug="delivery",
                          color="bg-blue-100 text-blue-700", icon="local_shipping",
                          sort_order=1, is_active=True)
        doc = FaqDoc(
            id=1, title="배송은 얼마나 걸리나요?",
            content="평균 2~3일 소요됩니다.",
            chroma_doc_id="faq_abc123", chroma_collection="faq",
            category="faq", extra_metadata="{}",
        )
        doc.faq_category = cat

        text = doc.to_chroma_document()
        assert text.startswith("[배송]")
        assert "Q: 배송은 얼마나 걸리나요?" in text
        assert "A: 평균 2~3일 소요됩니다." in text

    def test_to_chroma_document_without_category(self):
        """[GREEN] 카테고리 없는 문서는 prefix 없이 생성."""
        from app.models.faq_doc import FaqDoc

        doc = FaqDoc(
            id=2, title="일반 질문", content="일반 답변",
            chroma_doc_id="faq_xyz789", chroma_collection="faq",
            category="faq", extra_metadata="{}",
        )
        doc.faq_category = None

        text = doc.to_chroma_document()
        assert not text.startswith("[")
        assert "Q: 일반 질문" in text
        assert "A: 일반 답변" in text

    def test_to_chroma_metadata_includes_db_id(self):
        """[GREEN] ChromaDB 메타데이터에 db_id가 포함된다."""
        from app.models.faq_doc import FaqDoc

        doc = FaqDoc(
            id=5, title="Q", content="A",
            chroma_doc_id="faq_meta001", chroma_collection="faq",
            category="faq", extra_metadata='{"tags": ["배송"]}',
        )
        doc.faq_category = None

        meta = doc.to_chroma_metadata()
        assert meta["db_id"] == 5
        assert meta["chroma_doc_id"] == "faq_meta001"
        assert meta["tags"] == "배송"

    def test_to_chroma_metadata_with_invalid_extra(self):
        """[GREEN] extra_metadata 파싱 실패 시 빈 dict로 폴백."""
        from app.models.faq_doc import FaqDoc

        doc = FaqDoc(
            id=6, title="Q", content="A",
            chroma_doc_id="faq_bad_meta", chroma_collection="faq",
            category="faq", extra_metadata="INVALID_JSON",
        )
        doc.faq_category = None

        # 예외 없이 메타데이터 반환
        meta = doc.to_chroma_metadata()
        assert meta["db_id"] == 6


class TestFaqCategoryValidation:
    """FaqCategoryCreate Pydantic 스키마 검증."""

    def test_valid_slug_accepted(self):
        """[GREEN] 유효한 slug 형식은 그대로 통과."""
        from app.routers.faq import FaqCategoryCreate

        obj = FaqCategoryCreate(name="배송", slug="delivery-guide")
        assert obj.slug == "delivery-guide"

    def test_invalid_slug_raises(self):
        """[GREEN] 슬러그에 대문자 또는 특수문자 포함 시 ValueError."""
        from pydantic import ValidationError
        from app.routers.faq import FaqCategoryCreate

        with pytest.raises(ValidationError):
            FaqCategoryCreate(name="배송", slug="Delivery Guide!")

    def test_empty_name_raises(self):
        """[GREEN] 빈 name은 허용하지 않는다."""
        from pydantic import ValidationError
        from app.routers.faq import FaqCategoryCreate

        with pytest.raises(ValidationError):
            FaqCategoryCreate(name="   ", slug="test-slug")


class TestFaqCategoryRouter:
    """FAQ 카테고리 엔드포인트 통합 테스트."""

    def test_create_category_returns_201(self, client):
        """[GREEN] POST /api/admin/faq-categories → 201 + 생성된 카테고리."""
        resp = client.post("/api/admin/faq-categories", json={
            "name": "배송 안내",
            "slug": "delivery",
            "color": "bg-blue-100 text-blue-700",
            "icon": "local_shipping",
            "sort_order": 1,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["slug"] == "delivery"
        assert data["is_active"] is True
        assert data["doc_count"] == 0

    def test_duplicate_slug_returns_409(self, client):
        """[GREEN] 동일 slug 두 번 생성 시 409."""
        payload = {"name": "결제", "slug": "payment-dup"}
        client.post("/api/admin/faq-categories", json=payload)
        resp = client.post("/api/admin/faq-categories", json=payload)
        assert resp.status_code == 409

    def test_list_categories_returns_active_only(self, client):
        """[GREEN] include_inactive=false 시 활성 카테고리만 반환."""
        # 활성 카테고리 생성
        client.post("/api/admin/faq-categories", json={"name": "회원", "slug": "member"})
        resp = client.get("/api/admin/faq-categories")
        assert resp.status_code == 200
        cats = resp.json()
        assert all(c["is_active"] for c in cats)

    def test_delete_category_not_found(self, client):
        """[GREEN] 존재하지 않는 카테고리 삭제 시 404."""
        resp = client.delete("/api/admin/faq-categories/99999")
        assert resp.status_code == 404


class TestFaqDocRouter:
    """FAQ 문서 엔드포인트 통합 테스트."""

    def test_create_doc_returns_201(self, client):
        """[GREEN] POST /api/admin/faq-docs → 201."""
        resp = client.post("/api/admin/faq-docs", json={
            "title": "배송은 언제 오나요?",
            "content": "평균 2~3일 소요됩니다.",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "배송은 언제 오나요?"
        assert data["is_active"] is True
        assert data["chroma_doc_id"].startswith("faq_")

    def test_list_docs_returns_200(self, client):
        """[GREEN] GET /api/admin/faq-docs → 200."""
        resp = client.get("/api/admin/faq-docs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_doc_not_found(self, client):
        """[GREEN] 존재하지 않는 문서 조회 시 404."""
        resp = client.get("/api/admin/faq-docs/99999")
        assert resp.status_code == 404

    def test_update_doc_title(self, client):
        """[GREEN] PUT /api/admin/faq-docs/{id} — 제목 수정."""
        create_resp = client.post("/api/admin/faq-docs", json={
            "title": "원래 제목", "content": "답변",
        })
        doc_id = create_resp.json()["id"]

        resp = client.put(f"/api/admin/faq-docs/{doc_id}", json={"title": "수정된 제목"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "수정된 제목"

    def test_delete_doc_soft_delete(self, client):
        """[GREEN] DELETE /api/admin/faq-docs/{id} → is_active=False (소프트 삭제)."""
        create_resp = client.post("/api/admin/faq-docs", json={
            "title": "삭제할 문서", "content": "답변",
        })
        doc_id = create_resp.json()["id"]

        resp = client.delete(f"/api/admin/faq-docs/{doc_id}")
        assert resp.status_code == 204

        # 단건 조회 시 여전히 존재하되 is_active=False
        get_resp = client.get(f"/api/admin/faq-docs/{doc_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["is_active"] is False


# ──────────────────────────────────────────────────────────────────────────────
# Analytics 테스트: unanswered-samples / unused-faqs / top-cited
# ──────────────────────────────────────────────────────────────────────────────


class TestFaqAnalytics:
    """FAQ 인사이트/지표 엔드포인트 통합 테스트."""

    # ── action-summary ──────────────────────────────────────────────────────

    def test_analytics_action_summary_returns_200(self, client):
        """GET /api/admin/faq-analytics/action-summary → 200."""
        resp = client.get("/api/admin/faq-analytics/action-summary")
        assert resp.status_code == 200

    def test_analytics_action_summary_schema(self, client):
        """action-summary 응답은 필수 카운트 필드를 포함한다."""
        resp = client.get("/api/admin/faq-analytics/action-summary")
        assert resp.status_code == 200
        data = resp.json()
        for field in ("total_docs", "active_docs", "unanswered_count", "underperforming_count"):
            assert field in data, f"필드 누락: {field}"
        assert data["total_docs"] >= data["active_docs"]

    def test_analytics_action_summary_counts_active_docs(self, client):
        """action-summary.active_docs 는 is_active=True 문서 수를 정확히 반영한다."""
        before = client.get("/api/admin/faq-analytics/action-summary").json()["active_docs"]

        client.post("/api/admin/faq-docs", json={"title": "새 FAQ", "content": "답변"})
        after = client.get("/api/admin/faq-analytics/action-summary").json()["active_docs"]

        assert after == before + 1

    # ── unanswered-samples ──────────────────────────────────────────────────

    def test_analytics_unanswered_samples_returns_200(self, client):
        """GET /api/admin/faq-analytics/unanswered-samples → 200."""
        resp = client.get("/api/admin/faq-analytics/unanswered-samples")
        assert resp.status_code == 200

    def test_analytics_unanswered_samples_is_list(self, client):
        """unanswered-samples 응답은 리스트 형식이다."""
        resp = client.get("/api/admin/faq-analytics/unanswered-samples")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    # ── least-cited ─────────────────────────────────────────────────────────

    def test_analytics_least_cited_returns_200(self, client):
        """GET /api/admin/faq-analytics/least-cited → 200."""
        resp = client.get("/api/admin/faq-analytics/least-cited")
        assert resp.status_code == 200

    def test_analytics_least_cited_is_list_with_citation_count(self, client):
        """least-cited 응답은 citation_count 필드를 포함한 리스트다."""
        resp = client.get("/api/admin/faq-analytics/least-cited")
        assert resp.status_code == 200
        items = resp.json()
        assert isinstance(items, list)
        for item in items:
            assert "citation_count" in item

    def test_analytics_least_cited_respects_limit(self, client):
        """least-cited?limit=3 → 최대 3개 반환."""
        for i in range(6):
            client.post("/api/admin/faq-docs", json={
                "title": f"저인용 FAQ {i}", "content": "내용",
            })

        resp = client.get("/api/admin/faq-analytics/least-cited?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) <= 3

    def test_analytics_least_cited_sorted_asc(self, client):
        """least-cited 결과는 citation_count 오름차순이다."""
        resp = client.get("/api/admin/faq-analytics/least-cited")
        assert resp.status_code == 200
        items = resp.json()
        if len(items) >= 2:
            for i in range(len(items) - 1):
                assert items[i]["citation_count"] <= items[i + 1]["citation_count"]

    # ── top-cited ───────────────────────────────────────────────────────────

    def test_analytics_top_cited_returns_200(self, client):
        """GET /api/admin/faq-analytics/top-cited → 200 + 리스트."""
        resp = client.get("/api/admin/faq-analytics/top-cited")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_analytics_top_cited_respects_limit(self, client):
        """top-cited?limit=5 → 최대 5개 반환."""
        resp = client.get("/api/admin/faq-analytics/top-cited?limit=5")
        assert resp.status_code == 200
        assert len(resp.json()) <= 5

    # ── trending-questions ──────────────────────────────────────────────────

    def test_analytics_trending_questions_returns_200(self, client):
        """GET /api/admin/faq-analytics/trending-questions → 200."""
        resp = client.get("/api/admin/faq-analytics/trending-questions")
        assert resp.status_code == 200

    def test_analytics_trending_questions_schema(self, client):
        """trending-questions 응답은 period_days, total_questions, items 필드를 포함한다."""
        resp = client.get("/api/admin/faq-analytics/trending-questions")
        assert resp.status_code == 200
        data = resp.json()
        assert "period_days" in data
        assert "total_questions" in data
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_analytics_trending_questions_respects_days_param(self, client):
        """days 파라미터가 period_days 응답에 반영된다."""
        resp = client.get("/api/admin/faq-analytics/trending-questions?days=30")
        assert resp.status_code == 200
        assert resp.json()["period_days"] == 30

    def test_analytics_trending_questions_item_schema(self, client, db_session):
        """ChatLog 데이터가 있을 때 items 각 항목은 intent/intent_label/count/sample_question을 포함한다."""
        from app.models.chat_log import ChatLog
        from app.core.datetime_utils import now_kst

        # 테스트용 ChatLog 삽입 (user_id/session_id 없이)
        log = ChatLog(
            intent="delivery",
            question="배송 언제 오나요?",
            answer="2~3일 소요됩니다.",
            escalated=False,
            created_at=now_kst(),
        )
        db_session.add(log)
        db_session.commit()

        resp = client.get("/api/admin/faq-analytics/trending-questions?days=7&limit=10")
        assert resp.status_code == 200
        items = resp.json()["items"]

        delivery_items = [i for i in items if i["intent"] == "delivery"]
        assert len(delivery_items) >= 1
        item = delivery_items[0]
        for field in ("intent", "intent_label", "count", "sample_question"):
            assert field in item, f"필드 누락: {field}"
        assert item["count"] >= 1


class TestChatbotCorsErrorRegression:
    """Regression notes for chatbot-error-diagnosis.md.

    Process:
    - Browser symptom was reported as CORS blocked + POST /api/chatbot/ask 500.
    - ALLOW_ORIGINS already included http://localhost:5174, so the CORS value itself
      was not the primary failure.
    - The risky path was diagnostic ASGI middleware and BaseException wrapping around
      the chatbot route. Those can bypass normal FastAPI/Starlette exception handling
      and make a real 500 appear in the browser as a CORS failure.

    Result:
    - Remove the diagnostic ASGI middleware from app.main.
    - Do not catch BaseException in app.routers.chatbot; catch ordinary Exception only.
    - Keep CORS outside the router so even handled 500 responses include CORS headers.
    """

    def test_chatbot_ask_500_keeps_cors_header_for_frontend_origin(self):
        from app.core.config import settings
        from app.database import get_db
        from app.routers import chatbot as chatbot_router_module

        class FailingChatbotService:
            async def answer(self, *args, **kwargs):
                raise RuntimeError("forced chatbot failure")

        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        app.include_router(chatbot_router_module.router)

        def override_get_db():
            yield MagicMock()

        app.dependency_overrides[get_db] = override_get_db

        previous_service = chatbot_router_module._chatbot_service_instance
        chatbot_router_module.set_chatbot_service(FailingChatbotService())
        try:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post(
                    "/api/chatbot/ask",
                    headers={"Origin": "http://localhost:5174"},
                    json={"question": "배송비 얼마야?", "sessionId": None, "history": []},
                )
        finally:
            chatbot_router_module._chatbot_service_instance = previous_service

        assert resp.status_code == 500
        assert resp.headers["access-control-allow-origin"] == "http://localhost:5174"

    def test_chatbot_route_does_not_swallow_base_exception(self):
        import app.routers.chatbot as chatbot_module
        source = Path(chatbot_module.__file__).read_text(encoding="utf-8")
        assert "except BaseException" not in source

    def test_main_app_has_no_diagnostic_asgi_middleware_outside_cors(self):
        import app.main as main_module
        source = Path(main_module.__file__).read_text(encoding="utf-8")
        assert "_DiagASGIMiddleware" not in source
        assert "debug=True" not in source
