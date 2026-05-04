# ai/agent/faq_writer/

FAQ 자동 작성 에이전트. Gap Analyzer가 발견한 미등록 질문 패턴을 받아
LangChain tool calling 기반으로 FAQ 제목·답변 초안을 생성합니다.

---

## 파일 구조

| 파일 | 역할 |
|------|------|
| `agent.py` | `FaqWriterAgent` 클래스 + `FaqDraftResult` 데이터클래스 |
| `tools.py` | `build_faq_writer_tools()` 팩토리 — 3개 StructuredTool |
| `prompts.py` | `FAQ_WRITER_SYSTEM_PROMPT` — JSON 출력 규격 + citation 규칙 + `FAQ_TONE_POLICY` 적용 |

---

## 에이전트 흐름

```text
POST /api/admin/faq-analytics/generate-draft
  → generate_faq_draft_endpoint()
  → FaqWriterAgent.generate(db, representative_question, ...)
      1. search_faq_context  → 유사 기존 FAQ 검색 (문체·형식 참고)
      2. search_policy       → 정책 문서 검색 (배송·환불·결제 등)
      3. get_faq_categories  → 활성 카테고리 목록 조회
      → LLM이 JSON 출력 → _parse_result() → FaqDraftResult
```

최대 6회 반복 (`_MAX_ITERATIONS = 6`). Primary LLM 실패 시 `with_fallbacks([Claude])` 자동 전환.

---

## 도구 (tools.py)

### `search_faq_context`

ChromaDB `faq` 컬렉션에서 유사 기존 FAQ를 Hybrid 검색합니다.
반환: `"N. 질문: ... 답변: ..."` 형식 텍스트 (문체·수준 참고용)

```python
search_faq_context(query="배송 기간", top_k=3)
```

### `search_policy`

6개 정책 컬렉션에서 관련 청크를 검색하고 **인용 출처 JSON**을 함께 반환합니다.

```text
[인용출처]: {"doc": "반품교환환불정책", "chapter": "제1장 반품·교환·환불", "article": "제5조(반품 조건 및 배송비 부담)", "clause": "제1항"}
[내용]:
...정책 본문 앞 300자...
```

`_normalize_citation(meta, doc_text)` 헬퍼가 ChromaDB 실제 메타데이터 키(`doc_title`, `article`, `chapter`)를 읽어 정규화합니다.

### `get_faq_categories`

`shop_faq_categories` 테이블에서 활성 카테고리를 `slug: 이름` 형식으로 반환합니다.
LLM이 `suggested_category_slug` 선택에 사용합니다.

---

## `_normalize_citation` — ChromaDB 메타데이터 키 매핑

**중요**: 정책 컬렉션의 실제 메타데이터 키는 `doc_title`, `article`, `chapter`입니다.
`citation_doc`, `citation_article`, `citation_clause`는 존재하지 않으므로 사용하지 마세요.

| ChromaDB 실제 키 | 역할 | 예시 |
|-----------------|------|------|
| `doc_title` | 정책 문서명 | `"반품교환환불정책"` |
| `chapter` | 장 번호·이름 | `"제1장 반품·교환·환불"` (없으면 키 자체 없음) |
| `article` | 조 번호·이름 | `"제5조(반품 조건 및 배송비 부담)"` |
| *(없음)* | 항은 메타데이터 없음 | 문서 본문 앞 500자에서 `제N항` 패턴으로 추출 |

---

## 출력 형식 (LLM → JSON)

```json
{
  "title": "반품 신청 시 배송비는 누가 부담하나요?",
  "content": "고객 변심으로 반품 시 배송비는 고객이 부담합니다. ...",
  "suggested_category_slug": "exchange-return",
  "citation": {
    "doc": "반품교환환불정책",
    "chapter": "제1장 반품·교환·환불",
    "article": "제5조(반품 조건 및 배송비 부담)",
    "clause": "제3항"
  }
}
```

citation이 없으면 `null`.

---

## FaqDraftResult 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `title` | `str` | FAQ 제목 (질문 형태) |
| `content` | `str` | 순수 답변 — `(근거:...)` 미포함 |
| `suggested_category_slug` | `str \| None` | 추천 카테고리 slug |
| `model_used` | `str` | 사용된 LLM 모델명 |
| `citation_doc` | `str \| None` | 정책 문서명 |
| `citation_chapter` | `str \| None` | 장 번호·이름 |
| `citation_article` | `str \| None` | 조 번호·이름 |
| `citation_clause` | `str \| None` | 항 번호 |

어드민 DocFormModal에서 AI 초안 pre-fill로 활용됩니다.
content 말미의 `(근거: ...)` 삽입은 프론트엔드 `buildFinalContent()`가 담당합니다.

---

## API 엔드포인트

```http
POST /api/admin/faq-analytics/generate-draft
Content-Type: application/json

{
  "representative_question": "배송은 얼마나 걸려요?",
  "top_intent": "delivery",
  "gap_type": "missing",
  "count": 50,
  "escalated_count": 3
}

→ FaqDraftResponse {
    title, content,
    suggested_category_id, suggested_category_slug,
    model_used,
    citation_doc, citation_chapter, citation_article, citation_clause
  }
```

---

## 정책 인용 후보 API

FAQ 등록 모달의 장·조·항 드롭다운 데이터 공급:

```http
GET /api/admin/faq-analytics/policy-articles?doc=반품교환환불정책
→ [
    {"chapter": "제1장 반품·교환·환불", "article": "제5조(반품 조건 및 배송비 부담)", "clauses": ["제1항", "제2항", "제3항"]},
    ...
  ]
```

6개 정책 컬렉션을 순회하며 `doc_title == doc`인 청크의 메타데이터와 문서 본문을 분석합니다.
항(clause)은 문서 본문 앞 1000자에서 `제N항` 전체를 수집합니다.
