# 기술 의사결정: AI Tool 노출 — MCP vs LangChain

## 결론

> 팀 표준이 LangChain Agent였지만, 제가 담당한 **리뷰 자동화 분석 모듈은 MCP(FastMCP)로 10개 tool을 표준 노출**했습니다.
> 이는 "LangChain을 안 쓴 것"이 아니라, **LangChain·기타 AI 클라이언트가 모두 호출 가능한 한 층 위 표준 인터페이스**를 만든 것입니다.

---

## 검토한 대안

| 대안 | 채택 여부 | 한 줄 평가 |
|---|---|---|
| LangChain `create_agent` + tools | ❌ 보류 | 이미 갖춘 추상화와 중복, 마감 8일 전 도입은 위험 |
| LangFlow 시각적 빌더 | ❌ 비채택 | 별도 서버 + 플로우 JSON의 코드리뷰 부재 비용 큼 |
| **MCP (FastMCP) 직접 구현** | ✅ **채택** | 표준 호환·외부 클라이언트 즉시 연결·기존 구조와 직교 |

---

## 채택 근거 4가지

### 1. 표준 호환성 — MCP는 프로토콜, LangChain은 라이브러리
- MCP는 Anthropic이 정의한 프로토콜로, **Claude Desktop·Cursor·Cline·VSCode** 등에서 즉시 tool 호출 가능
- LangChain은 Python 라이브러리. MCP보다 한 층 아래 (애플리케이션 레벨)
- **`langchain-mcp-adapters`로 LangChain Agent가 MCP tool을 호출**할 수 있음 → 팀 표준과 양립 가능

### 2. 추상화 중복 회피
이미 갖춰진 추상화:
- `BaseLLMClient` (`backend/app/core/llm_client_base.py:34`) — Ollama / LiteLLM / RemoteOllama 팩토리
- `.env`의 `LLM_PROVIDER` 환경변수만으로 multi-provider 스왑

→ LangChain의 `init_chat_model` 효익이 이미 확보됨. LangChain을 한 겹 더 얹으면 **추상화 위에 추상화**가 되어 디버깅 난이도 ↑

### 3. 도메인 특화 로직은 어차피 직접 구현
LangChain Retriever/Chain으로 감싸도 다음 로직은 **그대로 다시 작성**해야 함:
- 한국어 리뷰용 **하이브리드 검색** (벡터 + 키워드 부스팅, `review_rag.py:277`)
- 시드 데이터 **텍스트 dedup** (`review_rag.py:336`)
- **멀티테넌트** seller_id 필터 (`mcp/tools.py:168`)
- **진행률 SSE** + `ctx.report_progress` (`mcp/tools.py:367`)
- **JSON 3단 폴백 파싱** + 재시도 (`review_analyzer.py:336`)

→ "프레임워크 도입으로 코드가 줄어든다"는 **성립하지 않음**

### 4. 마감 압박 (2026-05-11)
- 발표 8일 전 시점에서 LangChain v0.3 마이그레이션 이슈 + 의존성 5개+ 추가 = **회귀 테스트 부담**
- "발표 직전 신기술 도입"은 시연 실패의 1순위 원인
- **배포 안정성·데모 가능성**을 신기술 도입보다 우선시한 의사결정

---

## 부분 도입 계획 (Option A — 발표 후)

전면 도입은 보류했지만, **가장 ROI 높은 1곳**은 점진 적용 예정:

```python
# Before — review_analyzer.py:336 (3단 폴백 파싱 + 재시도)
def _parse_json_response(self, response: str) -> dict:
    try: return json.loads(text)
    except: pass
    if "```json" in text: ...   # 2단계
    first_brace = text.find("{") ...  # 3단계

# After — LangChain with_structured_output
class AnalysisResult(BaseModel):
    sentiments: list[Sentiment]
    keywords: list[Keyword]
    summary: Summary

result = await llm.with_structured_output(AnalysisResult).ainvoke(prompt)
# → 파싱 코드 ~50줄 제거, 스키마 강제로 안정성 ↑
```

**왜 이것만**: structured output은 LangChain의 가장 명확한 가치. 다른 영역은 추상화 비용이 효익보다 큼.

---

## MCP 노출 결과 (이미 구현 완료)

| Tool | 매핑 FastAPI 라우터 | 비고 |
|---|---|---|
| `embed_reviews` | `POST /api/v1/reviews/embed` | DB → ChromaDB 동기화 |
| `search_reviews` | `POST /api/v1/reviews/search` | 하이브리드 RAG |
| `analyze_reviews` | `POST /api/v1/reviews/analyze` | 단일 분석 |
| `analyze_reviews_with_progress` | `GET /api/v1/reviews/analyze/stream` | SSE 진행률 |
| `get_latest_analysis` | `GET /api/v1/reviews/analysis` | 최신 결과 |
| `get_analysis_by_id` | (신규) | 특정 ID 조회 |
| `get_trends` | `GET /api/v1/reviews/trends` | 주간 트렌드 |
| `generate_pdf_report` | `GET /api/v1/reviews/report/pdf` | base64 inline (5MB 가드) |
| `get_analysis_settings` | `GET /api/v1/reviews/settings` | 자동 분석 설정 |
| `update_analysis_settings` | `PUT /api/v1/reviews/settings` | 설정 변경 |

**FastAPI ↔ MCP 동일 싱글턴 공유** (`backend/app/core/review_singletons.py`):
```python
rag = ReviewRAG()              # 둘 다 같은 ChromaDB 클라이언트
analyzer = ReviewAnalyzer()    # 둘 다 같은 LLM 인스턴스
```

→ **한 번 짠 도메인 로직을 두 진입점(REST API + MCP tool)에서 동일 동작 보장**

---

## 학습 포인트 (포트폴리오에 강조할 부분)

1. **신기술 도입 = 의사결정 = 트레이드오프 분석**임을 체득
2. "할 수 있어도 지금은 안 한다"는 우선순위 판단력
3. 프로토콜(MCP)과 라이브러리(LangChain)의 추상화 층위 구분
4. 팀 표준과 다른 선택을 하되 **양립 가능성**을 확보 (`langchain-mcp-adapters`)
