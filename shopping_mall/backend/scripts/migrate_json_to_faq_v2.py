"""JSON 지식 파일 → FaqCategory + FaqDoc (v2 통합 FAQ) 마이그레이션.

기존 ai/data/*.json 파일을 읽어:
  1. FaqCategory 레코드 생성 (10개 카테고리)
  2. FaqDoc 레코드 생성 (faq_category_id 연결, chroma_collection="faq")
  3. ChromaDB faq 컬렉션에 동기화 (FaqSync.upsert)

카테고리 구조:
  이커머스 기본: order / delivery / exchange-return / membership / service
  농산물 특화: product-quality / certification / storage / season / origin

실행:
    cd shopping_mall/backend
    uv run python scripts/migrate_json_to_faq_v2.py

완료 후 BM25 인덱스 재빌드:
    uv run python ai/seed_rag.py --from-db

주의:
  - 이미 존재하는 FaqCategory(slug 기준)와 FaqDoc(chroma_doc_id 기준)은
    내용을 업데이트합니다 (멱등 실행 가능).
  - ChromaDB 동기화는 각 문서 upsert 후 즉시 실행됩니다.
  - 이전 migrate_knowledge_to_db.py (v1) 로 이관된 기록이 있어도 충돌 없음.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal
from app.models.faq_doc import FaqDoc, CHROMA_COLLECTION
from app.models.faq_category import FaqCategory
from app.services.faq_sync import FaqSync
from app.paths import AI_DATA_DIR


# ── 카테고리 정의 ─────────────────────────────────────────────────────────────
#
# slug는 search_faq 도구의 subcategory 파라미터로 사용됩니다.
# 변경하면 cs_tools.py SearchFaqInput 설명과 함께 업데이트하세요.
#
# 이커머스 기본 분류 (1~5) + 농산물 쇼핑몰 특화 (6~10)

CATEGORIES = [
    # ── 이커머스 기본 분류 ──────────────────────────────────────────────────
    {
        "slug": "order",
        "name": "주문·결제",
        "description": "주문 방법, 결제 수단, 할부, 쿠폰·적립금 사용, 현금영수증",
        "color": "bg-sky-100 text-sky-700",
        "icon": "shopping_cart",
        "sort_order": 1,
    },
    {
        "slug": "delivery",
        "name": "배송·물류",
        "description": "배송 일정·조회, 묶음배송, 새벽배송, 도서산간 추가요금",
        "color": "bg-emerald-100 text-emerald-700",
        "icon": "local_shipping",
        "sort_order": 2,
    },
    {
        "slug": "exchange-return",
        "name": "교환·반품·환불",
        "description": "반품 절차, 교환 조건, 환불 소요 시간, 배송비 부담 기준",
        "color": "bg-rose-100 text-rose-700",
        "icon": "assignment_return",
        "sort_order": 3,
    },
    {
        "slug": "membership",
        "name": "회원·적립금",
        "description": "회원 가입·탈퇴, 개인정보, 소셜 로그인, 등급·적립금·쿠폰",
        "color": "bg-violet-100 text-violet-700",
        "icon": "person",
        "sort_order": 4,
    },
    {
        "slug": "service",
        "name": "고객서비스",
        "description": "상담 채널·시간, 선물 포장, 재입고 알림, 정기 구독, 영수증",
        "color": "bg-rose-100 text-rose-700",
        "icon": "support_agent",
        "sort_order": 5,
    },
    # ── 농산물 쇼핑몰 특화 ─────────────────────────────────────────────────
    {
        "slug": "product-quality",
        "name": "상품·품질·신선도",
        "description": "신선도 보증, 중량 오차 기준, 품질 불량 인정 기준, 등급(특·상·보통)",
        "color": "bg-teal-100 text-teal-700",
        "icon": "verified",
        "sort_order": 6,
    },
    {
        "slug": "certification",
        "name": "인증·친환경",
        "description": "유기농·무농약·GAP 인증, 친환경 포장재, 알레르기 성분 확인",
        "color": "bg-emerald-100 text-emerald-700",
        "icon": "eco",
        "sort_order": 7,
    },
    {
        "slug": "storage",
        "name": "보관 방법",
        "description": "농산물별 냉장·냉동·실온 보관 가이드",
        "color": "bg-teal-100 text-teal-700",
        "icon": "ac_unit",
        "sort_order": 8,
    },
    {
        "slug": "season",
        "name": "제철·수확 정보",
        "description": "계절별 제철 농산물, 수확 시기, 저장 상품 안내",
        "color": "bg-amber-100 text-amber-700",
        "icon": "calendar_today",
        "sort_order": 9,
    },
    {
        "slug": "origin",
        "name": "원산지·농장",
        "description": "산지 직송, 협력 농가 소개, 원산지 확인, FarmOS 플랫폼 안내",
        "color": "bg-stone-100 text-stone-700",
        "icon": "agriculture",
        "sort_order": 10,
    },
]

# 이전 버전에서 사용하던 슬러그 — 신규 슬러그로 대체됨
# 마이그레이션 후 비활성화 처리된다.
_DEPRECATED_SLUGS = {"faq", "farm", "product", "policy"}

# ── 라우팅 맵 ─────────────────────────────────────────────────────────────────

# faq.json intent → category slug
_INTENT_TO_SLUG: dict[str, str] = {
    "delivery":   "delivery",
    "exchange":   "exchange-return",
    "cancel":     "exchange-return",
    "payment":    "order",
    "membership": "membership",
    "service":    "service",
    "product":    "product-quality",
    "order":      "order",
    "stock":      "service",
}

# policy_faq.json policy_type → category slug
# 'quality' 정책은 product-quality에 등록한다 (기존 'policy' 단일 분류 탈피)
_POLICY_TYPE_TO_SLUG: dict[str, str] = {
    "return":     "exchange-return",
    "payment":    "order",
    "quality":    "product-quality",
    "delivery":   "delivery",
    "membership": "membership",
    "service":    "service",
}

# product_faq.json category → category slug
_PRODUCT_CATEGORY_TO_SLUG: dict[str, str] = {
    "freshness":     "product-quality",
    "certification": "certification",
    "quality":       "product-quality",
    "safety":        "product-quality",
    "origin":        "origin",
    "delivery":      "delivery",
    "environment":   "certification",
}


# ── 공통 헬퍼 ─────────────────────────────────────────────────────────────────

def _load_json(filename: str) -> list:
    path = str(AI_DATA_DIR / filename)
    if not os.path.exists(path):
        print(f"  [건너뜀] 파일 없음: {path}")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _upsert_category(db, data: dict) -> FaqCategory:
    """slug 기준 upsert. 반환값: FaqCategory 인스턴스."""
    existing = db.query(FaqCategory).filter(FaqCategory.slug == data["slug"]).first()
    if existing:
        existing.name = data["name"]
        existing.description = data.get("description")
        existing.color = data["color"]
        existing.icon = data["icon"]
        existing.sort_order = data["sort_order"]
        existing.is_active = True
        print(f"  [업데이트] category: {data['slug']}")
        return existing

    cat = FaqCategory(**data, is_active=True)
    db.add(cat)
    db.flush()  # id 확보
    print(f"  [생성] category: {data['slug']} (id={cat.id})")
    return cat


def _upsert_doc(
    db,
    *,
    faq_category_id: int,
    chroma_doc_id: str,
    title: str,
    content: str,
    extra_metadata: dict,
) -> tuple[FaqDoc, bool]:
    """chroma_doc_id 기준 upsert. 반환값: (doc, is_created)."""
    existing = (
        db.query(FaqDoc)
        .filter(FaqDoc.chroma_doc_id == chroma_doc_id)
        .first()
    )
    if existing:
        existing.faq_category_id = faq_category_id
        existing.category = "faq"
        existing.chroma_collection = CHROMA_COLLECTION
        existing.title = title
        existing.content = content
        existing.extra_metadata = json.dumps(extra_metadata, ensure_ascii=False)
        existing.is_active = True
        return existing, False

    doc = FaqDoc(
        faq_category_id=faq_category_id,
        category="faq",
        chroma_collection=CHROMA_COLLECTION,
        chroma_doc_id=chroma_doc_id,
        title=title,
        content=content,
        extra_metadata=json.dumps(extra_metadata, ensure_ascii=False),
        is_active=True,
    )
    db.add(doc)
    db.flush()
    return doc, True


def _format_citation(citation: dict) -> str:
    """citation dict → '(근거: 문서명 제N조(...) 제N항)' 형식 문자열.

    Args:
        citation: {"doc": str, "article": str, "clause": str (optional)}
    """
    if not citation or not citation.get("doc"):
        return ""
    parts = [citation["doc"]]
    if citation.get("article"):
        parts.append(citation["article"])
    if citation.get("clause"):
        parts.append(citation["clause"])
    return f"\n(근거: {' '.join(parts)})"


# ── JSON → FaqDoc 변환 함수들 ────────────────────────────────────────────────

def migrate_faq(db, cat_map: dict[str, FaqCategory]) -> list[FaqDoc]:
    """faq.json → intent 기반으로 적절한 카테고리에 등록."""
    data = _load_json("faq.json")
    docs = []
    for item in data:
        intent = item.get("intent", "")
        slug = _INTENT_TO_SLUG.get(intent, "service")
        category = cat_map.get(slug, cat_map["service"])
        doc, created = _upsert_doc(
            db,
            faq_category_id=category.id,
            chroma_doc_id=item["id"],
            title=item["question"],
            content=item["answer"],
            extra_metadata={"intent": intent, "tags": intent},
        )
        docs.append(doc)
        status = "생성" if created else "업데이트"
        print(f"    [{status}] {item['id']} → {slug}: {item['question'][:30]}")
    return docs


def migrate_storage(db, cat_map: dict[str, FaqCategory]) -> list[FaqDoc]:
    """storage_guide.json → 보관 방법(storage)."""
    data = _load_json("storage_guide.json")
    category = cat_map["storage"]
    docs = []
    for item in data:
        doc, created = _upsert_doc(
            db,
            faq_category_id=category.id,
            chroma_doc_id=item["id"],
            title=f"{item['product']} 보관 방법",
            content=item["guide"],
            extra_metadata={
                "product_name": item["product"],
                "tags": f"{item['product']},보관,{item.get('category', '')}",
            },
        )
        docs.append(doc)
        print(f"    [{'생성' if created else '업데이트'}] {item['id']}: {item['product']}")
    return docs


def migrate_season(db, cat_map: dict[str, FaqCategory]) -> list[FaqDoc]:
    """season_info.json → 제철·수확 정보(season)."""
    data = _load_json("season_info.json")
    category = cat_map["season"]
    docs = []
    for item in data:
        products_str = ", ".join(item.get("products", []))
        content = f"{item['description']}\n제철 상품: {products_str}" if products_str else item["description"]
        doc, created = _upsert_doc(
            db,
            faq_category_id=category.id,
            chroma_doc_id=item["id"],
            title=f"{item['season']} 제철 농산물",
            content=content,
            extra_metadata={
                "season": item["season"],
                "tags": f"제철,{item['season']},{products_str}",
            },
        )
        docs.append(doc)
        print(f"    [{'생성' if created else '업데이트'}] {item['id']}: {item['season']}")
    return docs


def migrate_farm(db, cat_map: dict[str, FaqCategory]) -> list[FaqDoc]:
    """farm_info.json → 원산지·농장(origin)."""
    data = _load_json("farm_info.json")
    category = cat_map["origin"]
    docs = []
    for item in data:
        doc, created = _upsert_doc(
            db,
            faq_category_id=category.id,
            chroma_doc_id=item["id"],
            title=item["title"],
            content=item["content"],
            extra_metadata={
                "category": item.get("category", ""),
                "tags": f"농장,원산지,{item.get('category', '')},{item['title']}",
            },
        )
        docs.append(doc)
        print(f"    [{'생성' if created else '업데이트'}] {item['id']}: {item['title']}")
    return docs


def migrate_product(db, cat_map: dict[str, FaqCategory]) -> list[FaqDoc]:
    """product_faq.json → category 필드 기반으로 적절한 카테고리에 등록."""
    data = _load_json("product_faq.json")
    docs = []
    for item in data:
        prod_cat = item.get("category", "")
        slug = _PRODUCT_CATEGORY_TO_SLUG.get(prod_cat, "product-quality")
        category = cat_map.get(slug, cat_map["product-quality"])
        doc, created = _upsert_doc(
            db,
            faq_category_id=category.id,
            chroma_doc_id=item["id"],
            title=item["question"],
            content=item["answer"],
            extra_metadata={
                "product_category": prod_cat,
                "tags": f"농산물,{prod_cat},{item['question'][:20]}",
            },
        )
        docs.append(doc)
        status = "생성" if created else "업데이트"
        print(f"    [{status}] {item['id']} → {slug}: {item['question'][:35]}")
    return docs


def migrate_policy(db, cat_map: dict[str, FaqCategory]) -> list[FaqDoc]:
    """policy_faq.json → policy_type 기반 카테고리 등록 + 정책 문서 인용 삽입.

    각 항목의 citation 필드(doc·article·clause)를 답변 말미에
    '(근거: ...)' 형식으로 붙여 정책 출처를 명시합니다.
    """
    data = _load_json("policy_faq.json")
    docs = []
    for item in data:
        policy_type = item.get("policy_type", "")
        slug = _POLICY_TYPE_TO_SLUG.get(policy_type, "service")
        category = cat_map.get(slug, cat_map["service"])

        # 정책 문서 인용 텍스트 생성 (조·항 포함)
        citation = item.get("citation", {})
        citation_text = _format_citation(citation)
        content = item["answer"] + citation_text

        extra: dict = {
            "policy_type": policy_type,
            "tags": f"정책,{policy_type},{item['question'][:20]}",
        }
        if citation.get("doc"):
            extra["citation_doc"] = citation["doc"]
        if citation.get("article"):
            extra["citation_article"] = citation["article"]
        if citation.get("clause"):
            extra["citation_clause"] = citation["clause"]

        doc, created = _upsert_doc(
            db,
            faq_category_id=category.id,
            chroma_doc_id=item["id"],
            title=item["question"],
            content=content,
            extra_metadata=extra,
        )
        docs.append(doc)
        status = "생성" if created else "업데이트"
        cite_hint = f" [{citation.get('doc', '')} {citation.get('article', '')}]" if citation.get("doc") else ""
        print(f"    [{status}] {item['id']} → {slug}{cite_hint}: {item['question'][:30]}")
    return docs


# ── ChromaDB 동기화 ───────────────────────────────────────────────────────────

def sync_to_chroma(docs: list[FaqDoc]) -> tuple[int, int]:
    """활성 문서를 ChromaDB faq 컬렉션에 일괄 upsert.

    Returns:
        (synced, failed) — 성공 건수와 실패 건수.
    """
    synced = 0
    failed = 0
    for doc in docs:
        if not doc.is_active:
            continue
        try:
            FaqSync.upsert(doc)
            synced += 1
        except Exception as e:
            failed += 1
            print(f"    [ChromaDB 오류] {doc.chroma_doc_id}: {e}")
    return synced, failed


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  FAQ v2 마이그레이션 (JSON → DB → ChromaDB)")
    print("  카테고리: 이커머스 기본 5 + 농산물 특화 5")
    print("=" * 60)

    # 마이그레이션 완료 마커 확인
    marker = AI_DATA_DIR / ".migration_v2_complete"
    if marker.exists():
        print(f"\n[알림] 이미 실행된 마이그레이션입니다 ({marker.read_text().strip()})")
        print("  멱등 실행이므로 중단하지 않고 계속 진행합니다.")
        print("  (데이터 손상 없이 카테고리·문서를 업데이트합니다)")
        print()

    db = SessionLocal()
    try:
        # 1. FaqCategory 생성/업데이트
        print("\n[1/4] FaqCategory 생성 (10개)")
        cat_map: dict[str, FaqCategory] = {}
        for cat_data in CATEGORIES:
            cat = _upsert_category(db, cat_data)
            cat_map[cat_data["slug"]] = cat
        db.flush()

        # 1-b. 구 슬러그 비활성화
        #     (faq / farm / product / policy → 새 슬러그로 대체됨)
        print("\n  [구 슬러그 비활성화]")
        deprecated = (
            db.query(FaqCategory)
            .filter(FaqCategory.slug.in_(_DEPRECATED_SLUGS))
            .all()
        )
        for old_cat in deprecated:
            if old_cat.is_active:
                old_cat.is_active = False
                print(f"    비활성화: {old_cat.slug} (id={old_cat.id})")

        # 2. FaqDoc 이관
        print("\n[2/4] FaqDoc 이관")

        print("  → 일반 FAQ (faq.json) — intent 기반 카테고리 라우팅")
        faq_docs = migrate_faq(db, cat_map)

        print("  → 보관 방법 (storage_guide.json) → storage")
        storage_docs = migrate_storage(db, cat_map)

        print("  → 제철·수확 정보 (season_info.json) → season")
        season_docs = migrate_season(db, cat_map)

        print("  → 원산지·농장 (farm_info.json) → origin")
        farm_docs = migrate_farm(db, cat_map)

        print("  → 상품·품질 FAQ (product_faq.json) — category 기반 카테고리 라우팅")
        product_docs = migrate_product(db, cat_map)

        print("  → 정책 FAQ (policy_faq.json) — policy_type 기반 카테고리 라우팅 + 인용 삽입")
        policy_docs = migrate_policy(db, cat_map)

        db.commit()
        all_docs = faq_docs + storage_docs + season_docs + farm_docs + product_docs + policy_docs
        print(f"\n  DB 커밋 완료: 총 {len(all_docs)}건")

        # 3. ChromaDB 동기화
        print("\n[3/4] ChromaDB faq 컬렉션 동기화")
        doc_ids = [d.id for d in all_docs]
        from sqlalchemy.orm import joinedload
        fresh_docs = (
            db.query(FaqDoc)
            .options(joinedload(FaqDoc.faq_category))
            .filter(FaqDoc.id.in_(doc_ids))
            .all()
        )
        synced, failed = sync_to_chroma(fresh_docs)
        print(f"  ChromaDB 동기화: {synced}/{len(fresh_docs)}건 (실패: {failed}건)")

    except Exception as e:
        db.rollback()
        print(f"\n[오류] 롤백: {e}")
        raise
    finally:
        db.close()

    # 4. 결과 요약
    print("\n[4/4] 결과 요약")
    print("=" * 60)
    cat_counts: dict[str, int] = {}
    for doc_list, cat_slug in [
        (faq_docs, "intent별"),
        (storage_docs, "storage"),
        (season_docs, "season"),
        (farm_docs, "origin"),
        (product_docs, "category별"),
        (policy_docs, "policy_type별"),
    ]:
        cat_counts[cat_slug] = len(doc_list)

    print(f"""
  일반 FAQ       : {len(faq_docs):3d}건  (order/delivery/exchange-return/membership/service/product-quality)
  보관 방법      : {len(storage_docs):3d}건  → storage
  제철 정보      : {len(season_docs):3d}건  → season
  원산지·농장    : {len(farm_docs):3d}건  → origin
  상품·품질 FAQ  : {len(product_docs):3d}건  (product-quality/certification/origin/delivery)
  정책 FAQ       : {len(policy_docs):3d}건  (order/delivery/exchange-return/membership/service/product-quality)
  ChromaDB       : {synced:3d}건 동기화

다음 단계 — BM25 인덱스 재빌드:
  uv run python ai/seed_rag.py --from-db

이후부터는 어드민 UI(FaqPage)에서 FAQ를 관리하세요.
""")

    # 마이그레이션 완료 마커 저장 — ChromaDB 동기화 실패가 없을 때만 기록
    from app.core.datetime_utils import now_kst
    if failed == 0:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"completed at {now_kst().isoformat()}\n")
        print(f"  마이그레이션 완료 마커 저장: {marker}")
    else:
        print(f"\n[경고] ChromaDB 동기화 실패 {failed}건 — 완료 마커를 저장하지 않습니다.")
        print("  ChromaDB 오류를 확인 후 스크립트를 다시 실행하세요.")
        raise RuntimeError(f"ChromaDB 동기화 {failed}건 실패")


if __name__ == "__main__":
    main()
