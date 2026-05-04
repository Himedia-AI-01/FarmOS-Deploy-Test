# tests/

pytest 기반 테스트 스위트.

## 구조

```text
tests/
├── conftest.py               # 공용 픽스처 (FakeAgentClient, FakeRAGService, make_mock_db 등)
├── agent/
│   ├── test_executor.py          # AgentExecutor 단위 테스트
│   └── test_chatbot_service.py   # AgentChatbotService 통합 테스트
└── eval/                     # 정량 평가 실험 (LLM/ChromaDB/서버 호출 포함)
    ├── conftest.py               # Windows PyTorch OpenMP 환경변수 사전 설정
    ├── eval_dataset.json         # 라우팅 30건 + RAG 20건 레이블 데이터셋
    ├── test_routing_accuracy.py  # Experiment A: 의도 라우팅 정확도 (pytest)
    ├── test_rag_precision.py     # Experiment B: RAG Precision@3 (pytest)
    ├── test_edge_cases_llm.py    # Experiment C: Supervisor LLM 경계 케이스 7건 (standalone)
    ├── test_single_vs_multi_agent.py  # Experiment D: 단일 vs 멀티에이전트 라우팅 비교 (standalone)
    └── test_cs_response_quality.py   # Experiment E: CS 응답 품질 검증 (standalone, 서버 필요)
```

## 실행

```bash
# 전체 (mock 기반, 빠름)
uv run pytest

# 에이전트 단위 테스트만
uv run pytest tests/agent/

# Experiment A·B (ChromaDB 필요)
uv run pytest tests/eval/test_routing_accuracy.py tests/eval/test_rag_precision.py -v -s

# Experiment C — Supervisor LLM 경계 케이스 (LiteLLM 필요)
uv run python tests/eval/test_edge_cases_llm.py

# Experiment D — 단일 vs 멀티에이전트 비교 (LiteLLM 필요)
uv run python tests/eval/test_single_vs_multi_agent.py

# Experiment E — CS 응답 품질 검증 (서버 포트 4000 실행 중이어야 함)
uv run python tests/eval/test_cs_response_quality.py
```

## 픽스처 (conftest.py)

| 픽스처 | 설명 |
|--------|------|
| `FakeAgentClient` | 실제 LLM 호출 없이 미리 정의한 응답을 반환하는 mock 클라이언트 |
| `FakeRAGService` | ChromaDB 없이 컬렉션별 문서를 dict로 주입 |
| `make_mock_db` | SQLAlchemy Session mock |
| `make_text_response` | 텍스트 응답 AgentResponse 생성 헬퍼 |

---

## 정량 평가 실험 (tests/eval/)

서버·LLM 없이 실험 설계의 핵심 지표를 재현 가능하게 측정합니다.

### Experiment A - 라우팅 정확도 (test_routing_accuracy.py)

단일 에이전트 baseline과 Supervisor 멀티에이전트의 의도 오분류율을 비교합니다.

| 측정 항목 | 단일 에이전트 | Supervisor |
|-----------|:------------:|:----------:|
| 정확도 (N=30) | 53.3% | 100% |
| 오분류 건수 | 14건 | 0건 |
| **오분류율 감소** | - | **-46.7%p** |

- 단일 에이전트 취약점: `취소/교환/반품/환불` 키워드 존재 시 "주문 접수" 의도로 오분류
  - 예: "반품 방법이 뭐야?" → 정책 문의인데 주문 라우팅
- Supervisor 해결: `_fast_route()` 3단계 결정론적 라우팅
  - 1순위 CS 정책 키워드 (`방법/규정/조건/기간`) 우선 차단
  - 2순위 fastpath 패턴 (`취소해줘`, `교환 신청`)
  - 3순위 키워드-동사 근접 윈도우 (`교환` + 30자 이내 `하고 싶어`)

### Experiment B - RAG Precision@3 (test_rag_precision.py)

Dense 단독 검색과 Hybrid 검색(Dense + BM25 + RRF)의 Precision@3을 비교합니다.

| 그룹 | Dense P@3 | Hybrid P@3 | 격차 |
|------|:---------:|:----------:|:----:|
| BM25 strong (조항 번호·상품명) | 0.958 | 0.958 | 0.0%p |
| Dense strong (구어체·시맨틱) | 0.905 | 0.857 | -4.8%p |
| Mixed (정책+FAQ 혼합) | 0.933 | 1.000 | **+6.7%p** |
| **전체 평균 (N=20)** | **0.933** | **0.933** | **0.0%p** |

- 정책·FAQ 혼합 쿼리에서 Hybrid가 +6.7%p 우세 (BM25 정밀 키워드 매칭 효과)
- 구어체 시맨틱 쿼리는 Dense가 우세 (bge-m3 의미 임베딩)
- ChromaDB 미시딩 시 eval 테스트 전체 자동 SKIP

### Experiment E - CS 응답 품질 검증 (test_cs_response_quality.py)

실제 챗봇 서버에 6개 쿼리를 전송하고 CS 에이전트 응답의 품질 기준 충족 여부를 측정합니다.
서버(포트 4000)가 실행 중이어야 합니다.

#### 검증 기준 3종

| 검증 항목 | 기준 | 적용 케이스 |
|-----------|------|-------------|
| `handoff` | 반품/교환 신청 의사 → "교환과 반품·환불 중 원하시는 처리 방법을 알려주세요" + 선택지 1·2번 포함 | Case 1·2 |
| `bullet_limit` | 정책 응답의 불렛(- 항목) 수 ≤ 5개 | Case 3·4 |
| `citation` | `search_policy` 기반 응답에 `(근거: ...)` 형식 인용 포함 | Case 3·4·5·6 |

#### 케이스 목록

| ID | 쿼리 | 검증 |
|----|------|------|
| 1 | "반품 신청하고 싶어요" | handoff |
| 2 | "교환 신청하고 싶은데요" | handoff |
| 3 | "배송 기간에 관한 정책이 어떻게 되나요?" | bullet_limit, citation |
| 4 | "반품할 때 배송비는 누가 내나요?" | bullet_limit, citation |
| 5 | "상품 품질 보증 정책이 어떻게 되나요?" | citation |
| 6 | "결제 정책 규정상 지원되는 결제 수단과 제한 사항이 궁금해요" | citation |

#### 개선 이력 (2026-05-01)

**Before (수정 전)**
- 반품 신청 → Supervisor가 "비로그인 사용자 반품 정책 안내" 쿼리로 재작성 → CS가 정책 조문 나열
- 정책 응답 불렛 7~8개 나열 (조 단위 전체 재현)
- `(근거: ...)` 인용 불규칙 (reasoning_effort=low 에서 미준수 빈번)

**After (수정 후) — 전체 6케이스 8검증 기준**

| 쿼리 | 응답 시간 | handoff | bullet_limit | citation |
|------|-----------|:-------:|:------------:|:--------:|
| "반품 신청하고 싶어요" | 9~22s | ✅ (웜) / ⚠️ (콜드) | - | - |
| "교환 신청하고 싶은데요" | ~60s | ✅ | - | - |
| "배송 기간 정책이 어떻게 되나요?" | ~30s | - | ✅ (0~3개) | ✅ |
| "반품할 때 배송비는 누가 내나요?" | ~20s | - | ✅ | ✅ |
| "상품 품질 보증 정책이 어떻게 되나요?" | ~20s | - | - | ✅ |
| "결제 정책 규정상 지원되는 결제 수단..." | ~25s | - | - | ✅ |

**종합 통과율: 7/8 (87.5%)** — 웜 서버 기준 8/8 (100%)
- Case 1 간헐적 실패: Reranker 콜드 스타트(~80s) 시 LLM이 delivery/return 복합 정책을 나열, 핸드오프 형식 미적용. 웜 상태에서는 정상 통과.

**변경 파일**
- `ai/agent/supervisor/prompts.py` — 비로그인 신청 의사 → "교환·반품 신청 의사" 문구를 query에 포함하도록 지시
- `ai/agent/subagents/cs/prompts.py` — CS_INPUT_PROMPT / CS_OUTPUT_PROMPT 양쪽:
  - 핸드오프 트리거 조건 명확화 (신청 의사 키워드 목록 추가)
  - 불렛 5개 초과 시 관련 항목 3개만 제시, 정책 전문 복사 금지
  - 정책 인용 "반드시" 강조 + 구체적 예시 추가

#### tokenize_ko 개선 이력

| 버전 | 방식 | Hybrid avg P@3 | Dense 대비 |
|------|------|:--------------:|:----------:|
| v1 (단순 정규식) | 공백 분리 | 0.883 | **-5.0%p** |
| v2 (어미·조사 제거) | 한국어 형태소 접미사 규칙 | 0.933 | **0.0%p** |

한국어는 교착어이므로, 문서가 조항 단위로 나뉘어져 있더라도 조항 내부의 토큰 매칭에서
조사(`은/는/이/가/을/를` 등)와 어미(`한/하고/할` 등)를 제거하지 않으면 BM25가 매칭에 실패합니다.
예: 문서의 `"기간은"` ≠ 쿼리의 `"기간이"` → 둘 다 어근 `"기간"`으로 정규화해야 일치.
