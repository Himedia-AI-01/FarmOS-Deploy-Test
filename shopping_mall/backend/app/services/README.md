# app/services/

비즈니스 로직 레이어. 라우터에서 직접 DB를 다루지 않고 서비스를 통해 처리합니다.

## 파일

| 파일 | 역할 |
|------|------|
| `multi_agent_chatbot.py` | 챗봇 서비스 — `SupervisorExecutor` 래핑, ChatLog/ToolMetric 저장 |
| `ai_classifier.py` | 비용 항목 자동 분류 — Ollama LLM 또는 키워드 룰 폴백으로 `ExpenseEntry.category` 채움 |
| `ai_report.py` | 주간 리포트 생성 — 매출/비용/인기상품 집계 후 Ollama LLM으로 인사이트 텍스트 생성 |
| `demand_forecaster.py` | 수요 예측 |
| `faq_sync.py` | FAQ ChromaDB 동기화 — `FaqSync.upsert()` / `delete()` + BM25 인덱스 debounce 재빌드 |
| `order_processor.py` | 주문 자동 취소 — 미결제 주문 만료 처리, 스케줄러에서 주기적 호출 |
| `revenue_sync.py` | 주문 데이터 → `RevenueEntry` 동기화 |
| `rfm_analyzer.py` | RFM(Recency·Frequency·Monetary) 기반 고객 세그먼트 분석 |
| `shipping_tracker.py` | 배송 상태 추적 및 갱신 |

## 챗봇 흐름

```text
POST /api/chatbot/ask
  → chatbot.py (router)
  → MultiAgentChatbotService.answer()
  → SupervisorExecutor.run()
      ├── call_cs_agent  → AgentExecutor (CS_TOOLS 9개)
      └── call_order_agent → OrderGraph (LangGraph StateGraph)
  → ChatLog + ToolMetric 저장
```
