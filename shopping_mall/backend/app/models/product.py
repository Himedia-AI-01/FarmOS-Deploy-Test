from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

if TYPE_CHECKING:
    from .category import Category
    from .store import Store
    from .review import Review


class Product(Base):
    __tablename__ = "shop_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_rate: Mapped[int] = mapped_column(Integer, default=0)
    category_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("shop_categories.id"), nullable=True)
    store_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("shop_stores.id"), nullable=True)
    thumbnail: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    images: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    options: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    # ④ 재고 임계값 자동 제어 ─────────────────────────────────────────────
    # stock=0 이면 자동으로 False, 재고 복구 시 자동으로 True
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 최소 재고 알림 임계값: stock <= low_stock_threshold 이면 운영 대시보드에서 경고 표시
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    # ─────────────────────────────────────────────────────────────────────
    restock_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rating: Mapped[float] = mapped_column(Float, default=0.0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    sales_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    category: Mapped[Optional["Category"]] = relationship("Category", back_populates="products")
    store: Mapped[Optional["Store"]] = relationship("Store", back_populates="products")
    reviews: Mapped[List["Review"]] = relationship("Review", back_populates="product")
