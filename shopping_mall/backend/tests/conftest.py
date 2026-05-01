"""공통 픽스처."""
import os
from unittest.mock import MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

import pytest


# ── LangSmith 트레이싱 차단 ────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="session")
def disable_langsmith_tracing():
    """테스트 실행 중 LangSmith 트레이싱을 비활성화합니다.

    LANGCHAIN_TRACING_V2=true 환경에서 pytest를 돌리면 FakeRAGService·FakeLLM 등
    테스트 스텁의 호출이 LangSmith에 실제 트레이스로 기록됩니다.
    이 픽스처는 세션 전체에서 트레이싱을 끄고 세션 종료 시 원래 값으로 복원합니다.
    """
    _original = os.environ.get("LANGCHAIN_TRACING_V2")
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    yield
    if _original is None:
        os.environ.pop("LANGCHAIN_TRACING_V2", None)
    else:
        os.environ["LANGCHAIN_TRACING_V2"] = _original


# ── RAG 서비스 목 ─────────────────────────────────────────────────────────────

class FakeRAGService:
    """retrieve / retrieve_multiple 결과를 제어 가능한 RAG 스텁."""

    def __init__(self, results: dict[str, list[str]] | None = None):
        # collection → 반환할 문서 리스트. 없으면 빈 리스트.
        self._results: dict[str, list[str]] = results or {}

    def retrieve(
        self,
        question: str,
        collection: str,
        top_k: int = 3,
        distance_threshold: float = 0.5,
        where: dict | None = None,
    ) -> list[str]:
        return self._results.get(collection, [])[:top_k]

    def retrieve_multiple(
        self,
        question: str,
        collections: list[str],
        top_k_per: int = 3,  # executor._tool_search_policy top_k_per=3과 동기화
        distance_threshold: float = 0.5,  # distance_threshold는 실제 ChromaDB가 적용 — 스텁에서는 무시
    ) -> list[str]:
        docs = []
        seen = set()
        for col in collections:
            for doc in self._results.get(col, [])[:top_k_per]:
                if doc not in seen:
                    seen.add(doc)
                    docs.append(doc)
        return docs

    def retrieve_with_metadata(
        self,
        question: str,
        collection: str,
        top_k: int = 3,
        distance_threshold: float = 0.5,
        where: dict | None = None,
    ) -> list[tuple[str, dict]]:
        """(doc_text, metadata) 튜플 목록 반환 — search_faq 도구용."""
        docs = self._results.get(collection, [])[:top_k]
        return [(doc, {"id": f"{collection}:{i}"}) for i, doc in enumerate(docs)]

    def hybrid_retrieve(
        self,
        question: str,
        collections: list[str],
        top_k: int = 5,
        distance_threshold: float = 0.5,
        rrf_k: int = 60,
    ) -> list[str]:
        """Dense + Sparse 하이브리드 검색 스텁 — collection 결과를 top_k까지 합산."""
        docs = []
        seen = set()
        for col in collections:
            for doc in self._results.get(col, []):
                if doc not in seen:
                    seen.add(doc)
                    docs.append(doc)
                if len(docs) >= top_k:
                    return docs
        return docs


# ── DB 세션 목 ────────────────────────────────────────────────────────────────

def make_mock_db(orders=None, shipments=None, products=None, chat_session=None):
    """SQLAlchemy Session 체인 목.

    db.query(Model) 호출 시 모델 클래스별로 다른 목을 반환하여
    Order/Shipment/Product/ChatSession 쿼리가 서로 덮어쓰지 않도록 분리합니다.
    """
    from app.models.order import Order
    from app.models.shipment import Shipment
    from app.models.product import Product
    from app.models.chat_session import ChatSession

    db = MagicMock()

    # 모델별 독립 목 체인 생성
    order_mock = MagicMock()
    shipment_mock = MagicMock()
    product_mock = MagicMock()
    chat_session_mock = MagicMock()

    def query_side_effect(model):
        if model is Order:
            return order_mock
        if model is Shipment:
            return shipment_mock
        if model is Product:
            return product_mock
        if model is ChatSession:
            return chat_session_mock
        return MagicMock()

    db.query.side_effect = query_side_effect

    # ── Order 체인 ─────────────────────────────────────────────────
    # filter().order_by().limit().all() → orders (최근 3건)
    order_mock.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
        orders or []
    )
    # filter().options().order_by().limit().all() → orders (base_query 경로: selectinload 포함)
    order_mock.filter.return_value.options.return_value.order_by.return_value.limit.return_value.all.return_value = (
        orders or []
    )
    # filter().filter().order_by().limit().all() → orders (order_id 추가 필터)
    order_mock.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
        orders or []
    )
    # filter().options().filter().all() → orders (base_query + order_id 직접 필터)
    order_mock.filter.return_value.options.return_value.filter.return_value.all.return_value = (
        orders or []
    )
    # filter().all() → orders (단순 조회)
    order_mock.filter.return_value.all.return_value = orders or []
    # filter().count() → 전체 주문 수
    order_mock.filter.return_value.count.return_value = len(orders or [])
    # filter().filter().count() → order_id 추가 필터 시 주문 수
    order_mock.filter.return_value.filter.return_value.count.return_value = len(orders or [])

    # ── Shipment 체인 ──────────────────────────────────────────────
    # filter().first() → 첫 번째 shipment
    shipment_obj = (shipments or [None])[0]
    shipment_mock.filter.return_value.first.return_value = shipment_obj

    # ── Product 체인 ──────────────────────────────────────────────
    # filter().order_by().limit().all() → products
    product_mock.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
        products or []
    )
    # filter().limit().all() → products
    product_mock.filter.return_value.limit.return_value.all.return_value = products or []

    # ── ChatSession 체인 ───────────────────────────────────────────
    # filter().first() → chat_session
    chat_session_mock.filter.return_value.first.return_value = chat_session

    db.add = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()
    return db


# ── 도메인 오브젝트 팩토리 ───────────────────────────────────────────────────

def make_order(
    order_id=1,
    user_id=10,
    total_price=35000,
    status="shipped",
    created_at=None,
    items=None,
):
    order = MagicMock()
    order.id = order_id
    order.user_id = user_id
    order.total_price = total_price
    order.status = status
    order.created_at = created_at or datetime(2026, 4, 10, tzinfo=KST)
    order.shipping_address = "서울시 강남구"
    order.items = items or []
    return order


def make_shipment(
    order_id=1,
    carrier="CJ대한통운",
    tracking_number="1234567890",
    status="in_transit",
    expected_arrival=None,
):
    s = MagicMock()
    s.order_id = order_id
    s.carrier = carrier
    s.tracking_number = tracking_number
    s.status = status
    s.expected_arrival = expected_arrival or datetime(2026, 4, 12, tzinfo=KST)
    return s


def make_product(
    product_id=1,
    name="딸기",
    price=12000,
    discount_rate=0,
    stock=150,
    rating=4.5,
    review_count=32,
    sales_count=200,
    description="신선한 딸기",
):
    p = MagicMock()
    p.id = product_id
    p.name = name
    p.price = price
    p.discount_rate = discount_rate
    p.stock = stock
    p.rating = rating
    p.review_count = review_count
    p.sales_count = sales_count
    p.description = description
    p.restock_date = None
    return p


# ── 공통 픽스처 ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def disable_reranker(monkeypatch):
    """테스트에서 Reranker 모델 다운로드 의존성 제거.

    settings.reranker_model을 빈 문자열로 오버라이드하면
    rerank()가 즉시 docs[:top_k]를 반환하여 CrossEncoder 로드를 건너뜁니다.
    """
    monkeypatch.setattr("ai.rag.settings.reranker_model", "")


@pytest.fixture
def empty_rag():
    return FakeRAGService()


@pytest.fixture
def empty_db():
    return make_mock_db()
