import logging
from sqlalchemy.orm import Session, joinedload
from app.models.order import Order, OrderItem
from app.models.cart import CartItem
from app.models.product import Product

logger = logging.getLogger(__name__)


def create_order(
    db: Session,
    user_id: int,
    shipping_address: str | None = None,
    payment_method: str = "card",
):
    """장바구니 기반 주문 생성.

    ④ 재고 임계값 자동 제어:
    - 주문 수량만큼 Product.stock을 차감합니다 (행 잠금으로 동시 주문 경쟁 방지).
    - 차감 후 stock == 0 이면 Product.is_available = False 로 자동 전환합니다.
    - 재고 부족 상품이 포함된 경우 주문을 거절합니다.
    """
    cart_items = db.query(CartItem).filter(CartItem.user_id == user_id).all()
    if not cart_items:
        return None

    # ── 상품별 요청 수량 집계 ─────────────────────────────────────────────
    # 동일 product_id 카트 행이 여러 개인 경우 합산하여 oversell 방지
    requested_totals: dict[int, int] = {}
    for ci in cart_items:
        requested_totals[ci.product_id] = requested_totals.get(ci.product_id, 0) + ci.quantity

    # ── 재고 잠금 (with_for_update) ────────────────────────────────────────
    # 동시 주문 시 oversell 방지: 관련 상품 행을 일괄 잠금
    locked_products: dict[int, Product] = {
        p.id: p
        for p in (
            db.query(Product)
            .filter(Product.id.in_(requested_totals.keys()))
            .with_for_update()
            .all()
        )
    }

    # ── 재고 충분 여부 사전 검사 ───────────────────────────────────────────
    # 집계된 총 수량 기준으로 검사 — 개별 행 비교 시 동일 상품 중복 카트의 합산 초과를 놓치는 문제 수정
    for product_id, total_qty in requested_totals.items():
        product = locked_products.get(product_id)
        if product is None:
            logger.warning("[create_order] 상품 미존재: product_id=%d", product_id)
            db.rollback()
            return None
        if product.stock < total_qty:
            logger.warning(
                "[create_order] 재고 부족: product=%d stock=%d requested=%d",
                product_id, product.stock, total_qty,
            )
            db.rollback()
            return None

    # ── 주문 금액 계산 + OrderItem 구성 ───────────────────────────────────
    total_price = 0
    order_items: list[OrderItem] = []
    for ci in cart_items:
        product = locked_products[ci.product_id]
        discounted = product.price * (100 - product.discount_rate) // 100
        item_total = discounted * ci.quantity
        total_price += item_total
        order_items.append(
            OrderItem(
                product_id=ci.product_id,
                quantity=ci.quantity,
                price=item_total,
                selected_option=ci.selected_option,
            )
        )

    # ── 주문 생성 ──────────────────────────────────────────────────────────
    order = Order(
        user_id=user_id,
        total_price=total_price,
        status="pending",
        shipping_address=shipping_address,
        payment_method=payment_method,
    )
    db.add(order)
    db.flush()  # order.id 확보

    for oi in order_items:
        oi.order_id = order.id
        db.add(oi)

    # ── ④ 재고 차감 + is_available 자동 전환 ──────────────────────────────
    for ci in cart_items:
        product = locked_products[ci.product_id]
        product.stock -= ci.quantity
        product.sales_count += ci.quantity

        if product.stock == 0:
            product.is_available = False
            logger.info(
                "[create_order] 재고 소진 → is_available=False: product=%d",
                product.id,
            )
        elif product.stock <= product.low_stock_threshold:
            logger.info(
                "[create_order] 낮은 재고 경고: product=%d stock=%d threshold=%d",
                product.id, product.stock, product.low_stock_threshold,
            )

    db.query(CartItem).filter(CartItem.user_id == user_id).delete()
    db.commit()
    db.refresh(order)
    return order


def get_orders(db: Session, user_id: int):
    return (
        db.query(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.product))
        .filter(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .all()
    )


def get_order(db: Session, order_id: int):
    return (
        db.query(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.product))
        .filter(Order.id == order_id)
        .first()
    )
