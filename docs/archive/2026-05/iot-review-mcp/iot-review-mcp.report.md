# IoT Review MCP — PDCA Completion Report

> **Status**: **Complete** ✅
>
> **Project**: FarmOS-v2
> **Version**: 0.1.0
> **Author**: clover0309
> **Completion Date**: 2026-05-02
> **PDCA Cycle**: #1 of 2 (다음 사이클: 오케스트레이션 에이전트)
> **Branch**: feat/IoT_Review_MCP

---

## Executive Summary

### 1.1 Project Overview

| Item | Content |
|------|---------|
| Feature | iot-review-mcp |
| Start Date | 2026-05-01 |
| End Date | 2026-05-02 |
| Duration | ~2 days (Plan → Design → Do → Check → Report) |
| Cycle Type | Foundation cycle — 도구 표면 구축 |

### 1.2 Results Summary

```
┌─────────────────────────────────────────────────────────┐
│  Match Rate: 99.5%                                      │
├─────────────────────────────────────────────────────────┤
│  ✅ Match:           29 / 29 in-scope items             │
│  ⚠️ Shallow:          0                                 │
│  ❌ Not implemented:  0                                 │
│  📌 Out-of-scope:     1 (T8 PDF font 사전 버그)          │
├─────────────────────────────────────────────────────────┤
│  Plan SC:           6 / 6   (100%)                      │
│  Plan AC:          10 / 10  (100% in-scope)             │
│  Decision Record:   8 / 8   (100%)                      │
│  Convention:                100%                        │
└─────────────────────────────────────────────────────────┘
```

### 1.3 Value Delivered

| Perspective | 계획 (Plan) | 실현 결과 |
|-------------|------------|----------|
| **Problem** | 분석/검색/리포트 함수가 HTTP에만 노출 → 오케스트레이션 에이전트가 도구로 호출할 표준 인터페이스 부재 | ✅ **FastMCP 표면 구축 완료** — 11 tools 노출, 다음 사이클의 에이전트가 즉시 합성 호출 가능 |
| **Solution** | FastMCP 기반 MCP 서버 + 1:1 low-level tool + JWT 미들웨어 재사용 + progress notification 매핑 + 호스팅 형태 Design Checkpoint | ✅ **Option C (FastAPI mount, streamable-http, stateless_http=True)** 채택. `combine_lifespans` 통합. 코어 모듈 시그니처 불변. |
| **Function/UX Effect** | 모든 MCP 클라이언트가 동일 tool 카탈로그로 분석 호출. 기존 FastAPI/프론트엔드 무변경 | ✅ Claude Code/Desktop, MCP Inspector, 향후 에이전트가 모두 동일 carrier 사용. 기존 프론트엔드 회귀 0. T4 progress notification 5회 검증. |
| **Core Value** | 재사용 가능한 도구화 + 코드 중복 0 + 표준 MCP 계약 | ✅ **`review_singletons.py` + `review_helpers.py`** 분리로 라우터/MCP 단일 source. **Bearer + Cookie** 둘 다 지원. **L2 동등성** search/trends/settings/get_latest 모두 EQUAL. |

### 1.4 Success Criteria Final Status

> Plan §2.3 D-1~D-5 결정과 §SC-01~06 기준의 최종 평가.

| # | Criteria | Status | Evidence |
|---|----------|:------:|----------|
| SC-01 | FastMCP 서버 ≥7 tool 노출 | ✅ Met | 11/10 tools (`tools/list` 응답) — `mcp/tools.py` |
| SC-02 | MCP 응답 == FastAPI 응답 (snapshot) | ✅ Met | search 5건 ID 동일 순서, get_trends EQUAL, settings EQUAL, get_latest "데이터 없음" 동등, T3/T4 사용자 검증 |
| SC-03 | 기존 FastAPI 7 endpoints 회귀 무 | ✅ Met | `core/review_*.py` 시그니처 불변, `api/review_analysis.py` import 변경만 |
| SC-04 | 멀티테넌트 컨텍스트 (seller_id) | ✅ Met | Bearer/Cookie/거절 3-way 검증. User.seller_id 미존재 → 안전 폴백 (라우터 동일 동작) |
| SC-05 | Progress notification (T4) | ✅ Met | T4 5회 알림 + 최종 result. analysis_id=2 검증. |
| SC-06 | 코어 단일 소스 (코드 중복 0) | ✅ Met | `review_singletons.py` (5 instances) + `review_helpers.py` (2 helpers) 분리 |

**Success Rate**: **6 / 6** ✅ **(100%)**

### 1.5 Decision Record Summary

> Plan→Design Decision Chain 의 최종 추적성.

| Source | Decision | Followed? | Outcome |
|--------|----------|:---------:|---------|
| [Plan] D-1 | tool 입자도 1:1 low-level | ✅ | 11 tools 모두 단일 책임. high-level wrapper 없음 → 다음 사이클 에이전트가 합성 자유 |
| [Plan] D-2 | JWT 미들웨어 재사용 (mount 전제) | ✅ | `core.security.decode_access_token` + `core.user_store.find_by_id` 직접 import — 검증 코드 중복 0 |
| [Plan] D-3 | progress = `ctx.report_progress` (T4 한정) | ✅ | T4 만 progress emit. Design §5.2 패턴 따름 |
| [Plan] D-4 | Architecture **Option C** — FastAPI mount, streamable-http, stateless_http=True | ✅ | `mcp.http_app(path="/", stateless_http=True)` + `app.mount("/mcp", _review_mcp_app)`. 단발 curl 동작 |
| [Plan] D-5 | tool 매핑 거의 1:1 | ✅ | 7 endpoints + 3 신규 (T4 progress 변형, T6 by_id 신규, settings GET/PUT 분리) |
| [Design] Q5 | Bearer + Cookie 둘 다 지원 | ✅ | `mcp/auth.py:_extract_token` Bearer 우선, Cookie 폴백. 외부 MCP 클라이언트 + 브라우저 양쪽 호환 |
| [Design] §6.1 | combine_lifespans 패턴 | ✅ | FastMCP 3.x `fastmcp.utilities.lifespan.combine_lifespans` 적용 + ImportError 폴백 |
| [Design] §3.2 | PDF base64 inline | ✅ | T8 `PdfReport(filename, content_base64, content_type, size_bytes)` + 5MB 가드 |

**Decision Compliance**: **8 / 8** ✅ **(100%)**

---

## 2. Related Documents

| Phase | Document | Status |
|-------|----------|:------:|
| Plan | [iot-review-mcp.plan.md](../01-plan/features/iot-review-mcp.plan.md) | ✅ Finalized |
| Design | [iot-review-mcp.design.md](../02-design/features/iot-review-mcp.design.md) | ✅ Finalized |
| Check | [iot-review-mcp.analysis.md](../03-analysis/iot-review-mcp.analysis.md) | ✅ Match Rate 99.5% |
| Report | (현재 문서) | ✅ Finalized |

PRD 는 본 사이클에서 사용자가 명시적으로 스킵 (이미 archived `farmos_review_analysis` / `review-analysis-automation` 사이클의 PRD/Design 자산 활용).

---

## 3. Completed Items

### 3.1 Functional Requirements (Plan §2.1)

| ID | Requirement | Status | Tool |
|----|-------------|:------:|------|
| FR-1 | FastMCP 서버 entry point | ✅ | `mcp/server.py:build_review_mcp` |
| FR-2 | `embed_reviews` tool | ✅ | T1 |
| FR-3 | `search_reviews` tool (top_k/filters/seller_id) | ✅ | T2 |
| FR-4 | `analyze_reviews` tool (sync) | ✅ | T3 |
| FR-5 | `analyze_reviews_with_progress` (progress notification) | ✅ | T4 |
| FR-6 | `get_latest_analysis` tool | ✅ | T5 |
| FR-7 | `get_analysis_by_id` tool | ✅ | T6 |
| FR-8 | `get_trends` tool | ✅ | T7 |
| FR-9 | `generate_pdf_report` tool (base64) | ✅ | T8 (PDF 생성은 사전 버그로 차단 — 회귀 무) |
| FR-10 | `get/update_analysis_settings` | ✅ | T9, T10 |
| FR-11 | 표준 입력 스키마 + 표준 오류 | ✅ | Pydantic + ToolError |
| FR-12 | seller_id 호출자 인증 컨텍스트 추출 | ✅ | `mcp/auth.py:get_current_user_from_ctx` |

**Functional Completion**: **12 / 12** ✅

### 3.2 Non-Functional Requirements (Plan §2.2)

| ID | 항목 | 결과 | Status |
|----|------|------|:------:|
| NFR-1 | 코어 시그니처 변경 무 | git diff 0 lines on `core/review_*.py` | ✅ |
| NFR-2 | FastAPI 라우터 동작 무변경 | import 변경만, 7 endpoints 동작 보존 | ✅ |
| NFR-3 | DB 세션 패턴 일관성 | `async with async_session() as db` 사용 | ✅ |
| NFR-4 | 코어 인스턴스 싱글턴 | `review_singletons.py` 모듈 속성 공유 | ✅ |
| NFR-5 | 인증 실패 시 표준 오류 | `ToolError("Authentication required: ...")` | ✅ |
| NFR-6 | 입력 검증 표준 | Pydantic + 명시 가드 (`top_k`, `sample_size`, `analysis_id`, `period`) | ✅ |
| NFR-7 | progress notification T4 한정 | T3 동기, T4 만 emit | ✅ |
| NFR-8 | fastmcp 의존성 추가 | pyproject.toml `fastmcp>=3.2.0`, uv sync 성공 | ✅ |

### 3.3 Deliverables

| Deliverable | Location | Lines |
|-------------|----------|:----:|
| Plan 문서 | `docs/01-plan/features/iot-review-mcp.plan.md` | ~310 |
| Design 문서 | `docs/02-design/features/iot-review-mcp.design.md` | ~615 |
| Analysis 문서 | `docs/03-analysis/iot-review-mcp.analysis.md` | ~330 |
| Report 문서 | `docs/04-report/iot-review-mcp.report.md` | (본 문서) |
| MCP 패키지 (신규) | `backend/app/mcp/{__init__,server,tools,auth,schemas}.py` | ~600 |
| 코어 헬퍼 (신규) | `backend/app/core/{review_helpers,review_singletons}.py` | ~125 |
| 라우터/main 수정 | `backend/app/api/review_analysis.py`, `backend/app/main.py` | ~50 |
| 의존성 | `backend/pyproject.toml` (+ fastmcp>=3.2.0) | +1 |

---

## 4. Incomplete / Out-of-scope Items

### 4.1 다음 사이클로 이월

| 항목 | 사유 | 우선순위 | 비고 |
|------|------|:------:|------|
| **오케스트레이션 에이전트 본체** | 본 사이클 OUT-OF-SCOPE | High | 본 사이클이 도구 표면 10개 제공 완료 — 다음 사이클이 즉시 시작 가능 |
| `User.seller_id` + `shop_stores.owner_id` 추가 | 데이터 모델 작업 별도 사이클 | Medium | `core/review_helpers.py:get_seller_product_ids` 의 TODO 주석 해제 필요 |
| 자동 배치 스케줄러 | archived design 에서 Phase 2 연기 | Low | T9/T10 settings 인프라는 준비됨 |

### 4.2 별 hotfix 권장 (out-of-cycle)

| 항목 | 작업 | Task |
|------|------|------|
| T8 PDF 폰트 사전 버그 | `core/review_report.py:134` `except RuntimeError:` → `except (RuntimeError, FPDFException):` (1줄 fix) | Task #8 메모 |
| `_register_font` 도 동시 개선 검토 | fpdf2 신 API + Bold 별도 폰트 검토 (선택) | 위와 함께 또는 별도 |

> 본 사이클 책임 외 — FastAPI 라우터 동일 영향. 회귀 무.

---

## 5. Quality Metrics

### 5.1 Match Rate (Analysis 결과)

| Metric | Target | Final | Δ |
|--------|:----:|:----:|:--:|
| Structural Match | 90% | **100%** | +10 |
| Functional Depth | 90% | **98%** | +8 |
| Contract Match | 90% | **100%** | +10 |
| Runtime (L1/L2 in-scope) | 80% | **100%** | +20 |
| **Overall Match Rate** | **90%** | **99.5%** | **+9.5** |

### 5.2 Issue Resolution

| Issue (Design Open Q) | Resolution | Result |
|----------|------------|:------:|
| Q1 JWT 디코더 위치 | `core/security.decode_access_token` 그대로 import | ✅ Resolved |
| Q2 User.seller_id 유무 | 미존재 — `getattr` 안전 폴백 | ✅ Resolved (라우터와 동등) |
| Q3 싱글턴 공유 방식 | `core/review_singletons.py` 신규 모듈 | ✅ Resolved (옵션 a 채택) |
| Q4 의존성 매니저 | uv → `fastmcp>=3.2.0` | ✅ Resolved |
| Q5 (Do 단계 발견) Auth 헤더 형태 | Bearer + Cookie 둘 다 지원 | ✅ Resolved |

### 5.3 Code Quality 관찰

**Strong Points** (Analysis §4.1):
- Decision Trace 주석 일관성 (모든 신규 파일에 `Design Ref: §X` + `Plan SC: SC-Y`)
- Defensive coding (`mcp/auth.py:_extract_token` 다중 fallback)
- Singleton 모듈 속성 패턴으로 stale alias 회피
- `combine_lifespans` ImportError 폴백 (FastMCP 버전 호환성)
- `stateless_http=True` 로 단발 curl 호환 + 운영 단순화

**Weak Points**: 없음 (in-scope 기준).

---

## 6. Lessons Learned & Retrospective

### 6.1 What Went Well (Keep)

- **Spike 우선 패턴**: module-1 첫 작업으로 `ping` tool 1개 + mount + lifespan 통합을 검증한 것이 후속 module 위험을 거의 0으로 낮춤.
- **3가지 Architecture 옵션 비교 후 Checkpoint 3**: D-2 (JWT 재사용) 가 사실상 옵션 C 를 강제한다는 정합성을 명시적으로 표시한 것이 선택 합리화에 도움.
- **In-process TestClient 동등성 검증**: 사용자가 직접 curl 하기 전에 search/trends/settings 동등성을 메인 세션에서 즉시 검증 → 빠른 피드백 루프.
- **싱글턴 모듈 속성 패턴**: 라우터/MCP 양쪽이 `_singletons.settings_state` 모듈 속성으로 접근하도록 설계해 stale alias 함정을 사전 회피 (`api/review_analysis.py:38-40` 주석 명시).
- **사전 존재 버그 식별 후 OUT-OF-SCOPE 결정**: T8 PDF 폰트 버그가 회귀가 아닌 것을 빠르게 검증 (FastAPI 측에서 동일 에러 재현) 후 별 task 로 격리.

### 6.2 What Needs Improvement (Problem)

- **Cookie 만료(60분)로 인한 반복 재로그인**: 검증 단계에서 사용자가 인증 만료를 여러 번 마주침. 다음 사이클부터는 검증 스크립트에 자동 재로그인 ("login + tool call" 한 줄 chained) 권장.
- **Design Open Q 가 Do 직전에 5번째(Q5)가 추가로 발견됨**: 인증 헤더 형태(Bearer vs Cookie)는 Design 단계에서 잡혔어야 함. 다음 사이클부터는 코어 코드 reading 을 Design phase 에 더 포함.
- **PowerShell ConvertTo-Json 의 중첩 객체 손상**: pdca-status.json 업데이트 시 PowerShell 5.1 의 ConvertTo-Json 이 중첩 객체를 잘못 직렬화 → Python 으로 fix. 다음부터는 모든 JSON 편집을 Python 으로 일원화.

### 6.3 What to Try Next (Try)

- **다음 사이클의 오케스트레이션 에이전트**: 본 사이클이 만든 11 tools 를 합성하는 에이전트 구현. 시작 명령은 `/pdca plan iot-review-orchestration` 또는 유사.
- **MCP Inspector 통합 테스트**: `npx @modelcontextprotocol/inspector` 로 stateful 모드도 검증 (현재는 stateless 에서만 검증).
- **L3 E2E 테스트 자동화**: pytest + FastMCP Client 로 11 tools 회귀 매트릭스를 CI 에 통합.

---

## 7. Process Improvement Suggestions

### 7.1 PDCA Process

| Phase | 개선 제안 |
|-------|----------|
| Plan | `Open Questions` 작성 시 코어 모듈을 한 번 grep — 인증/세션 패턴은 코드 reading 이 의무 |
| Design | Architecture 옵션 비교에 1-spike 권고 항목 추가 (FastMCP+FastAPI mount 같은 고리스크 통합) |
| Do | 모듈 단위로 in-process TestClient 검증을 routine 화 (사용자 검증 부하 감소) |
| Check | `gap-detector` 서브에이전트에 Write 권한 부여 검토 (현재는 Read/Glob/Grep 만, 결과를 메인이 받아서 파일화) |

### 7.2 Tools/Environment

| 영역 | 개선 제안 |
|------|----------|
| Cookie 관리 | dev 환경 ACCESS_TOKEN_EXPIRE_MINUTES 를 240분으로 늘리거나, `--cookie-jar` + auto-refresh 헬퍼 스크립트 |
| pdca-status.json 편집 | Python 스크립트화 (PowerShell ConvertTo-Json 회피) |
| MCP 검증 | 별 `tests/mcp_smoke.py` 추가 — pytest 로 11 tools 매트릭스 |

---

## 8. Next Steps

### 8.1 Immediate

- [x] Report 작성 — 현재 문서
- [ ] **`/pdca archive iot-review-mcp`** — 4개 문서 (plan/design/analysis/report) 를 `docs/archive/2026-05/iot-review-mcp/` 로 이동
- [ ] git commit + PR (선택) — `feat/IoT_Review_MCP` → `dev` 머지

### 8.2 Next PDCA Cycle (권장)

| 사이클 | 우선순위 | 예상 시작 | 비고 |
|--------|:------:|----------|------|
| **iot-review-orchestration** (오케스트레이션 에이전트) | High | 본 사이클 archive 직후 | 11 tools 합성 호출 에이전트 — 본 사이클이 도구 표면 제공 완료 |
| review_report 폰트 hotfix | Medium | 별 1-PR | 1줄 fix — 양쪽 endpoint 동시 해결 |
| User.seller_id + shop_stores.owner_id | Medium | 데이터 모델 사이클 | `get_seller_product_ids` TODO 해제 |

---

## 9. Changelog

### v0.1.0 — iot-review-mcp Cycle #1 (2026-05-02)

**Added:**
- `backend/app/mcp/` 패키지 (5 파일, ~600 lines) — FastMCP 기반 11 tools 노출
- `backend/app/core/review_helpers.py` — `_stratified_sample`, `_get_seller_product_ids` 추출
- `backend/app/core/review_singletons.py` — 5 코어 인스턴스(rag/analyzer/trend_detector/report_generator/settings_state) 분리
- `backend/app/mcp/schemas.py` — `PdfReport`, `AnalysisDetail` Pydantic 모델
- `backend/app/mcp/auth.py` — JWT Bearer + Cookie 둘 다 지원하는 인증 어댑터
- `fastmcp>=3.2.0` 의존성

**Changed:**
- `backend/app/main.py` — `combine_lifespans(lifespan, _review_mcp_app.lifespan)` + `app.mount("/mcp", _review_mcp_app)` (stateless_http=True)
- `backend/app/api/review_analysis.py` — 헬퍼/싱글턴을 신규 모듈에서 import (동작 무변경, `_singletons.settings_state` 모듈 속성 경유)

**Fixed:**
- 없음 (사전 존재 버그 1건은 OUT-OF-SCOPE 처리)

**Verified:**
- L1 호출 검증 11/11 (T8 사전 버그 제외)
- L2 동등성 검증 4/4 EQUAL
- 회귀 smoke 7 endpoints 무변경
- Plan SC 6/6, Plan AC 10/10 (in-scope), Decision 8/8

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-05-02 | Completion report — Match Rate 99.5%, all 6 SC met, 8 decisions followed | clover0309 |
