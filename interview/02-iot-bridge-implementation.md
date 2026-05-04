# IoT 적재 Bridge 구현 정리

## 한 줄 요약

> **외부 Relay에서 수행되는 AI Agent의 "결정(decision)"을 FarmOS Postgres에 무결하게 미러링**하는 ETL 모듈.
> SSE 실시간 + HTTP backfill 이중 채널, 멱등 UPSERT, exponential backoff, 시간/일별 자동 집계로 구성.

> **중요한 책임 경계**: FarmOS는 결정을 만들지 않습니다. 결정은 외부 Relay(별도 서버)에서 수행되고,
> FarmOS Bridge는 그 결과를 미러링·집계해 읽기 전용 API로 노출합니다.

---

## 아키텍처 다이어그램

```
[외부 Relay 서버]                    [FarmOS Backend]
─────────────────                    ─────────────────
                                     ┌───────────────────────────┐
 AI Agent 결정 수행                   │ AiAgentBridge (asyncio)   │
        │                            │                           │
        ├── SSE  /sensors/stream ───▶│ _connect_and_stream       │
        │   (event: ai_decision)     │   ├─ JSON parse           │
        │                            │   └─ _handle_decision     │
        │                            │       │                   │
        └── HTTP /ai-agent/decisions ◀── _backfill_since_last     │
            (keyset pagination)      │   ├─ since + after_id     │
                                     │   └─ 중복 무해             │
                                     │           │               │
                                     │           ▼               │
                                     │  _upsert_and_summarize    │
                                     │   ├─ INSERT ON CONFLICT   │
                                     │   │   (id) DO NOTHING     │
                                     │   ├─ _bump_daily          │
                                     │   └─ _bump_hourly         │
                                     └───────────┬───────────────┘
                                                 ▼
                       ┌──────────────────────────────────────────┐
                       │  Postgres                                │
                       │   ai_agent_decisions  (원본, 30일 TTL)   │
                       │   ai_agent_activity_daily  (일 집계)     │
                       │   ai_agent_activity_hourly (시 집계)     │
                       └──────────────────────────────────────────┘
                                                 │
                                                 ▼
                                     [FastAPI 읽기 전용 API]
                                       /ai-agent/activity/summary
                                       /ai-agent/decisions
                                       /ai-agent/decisions/{id}
                                       /ai-agent/activity/hourly
                                       /ai-agent/bridge/status
```

---

## 핵심 파일

| 파일 | 역할 |
|---|---|
| `backend/app/services/ai_agent_bridge.py` | Bridge 본체 (SSE 구독 + HTTP backfill + UPSERT + 집계) |
| `backend/app/api/ai_agent.py` | 읽기 전용 API (요약/목록/상세/시간별/Bridge 헬스) |
| `backend/app/core/sensor_filter.py` | 센서값 이상치 필터 (이동평균 + 조도 0 streak) |
| `backend/app/models/ai_agent.py` | ORM 모델 (`AiAgentDecision`, `AiAgentActivityDaily/Hourly`) |
| `backend/app/main.py` (lifespan) | Bridge `start()`/`stop()` 등록 |

---

## 설계 결정 5가지

### 1. 이중 채널 (SSE + HTTP backfill)
**Why**: SSE만 쓰면 재기동 동안 발생한 결정을 놓침. HTTP만 쓰면 실시간성 떨어짐.

**How**:
- 기동 시 `_backfill_since_last()` → DB의 마지막 `(timestamp, id)` 이후 결정을 페이지네이션으로 당겨옴
- 그 후 `_connect_and_stream()` → SSE 구독으로 실시간 이벤트 수신
- 둘이 중복돼도 멱등 INSERT로 무해

**Code**: `ai_agent_bridge.py:133` (`_run_loop`)

### 2. 복합 cursor keyset pagination
**Why**: 동일 timestamp에 다중 행이 페이지 경계에 걸리면 누락/중복 발생. `OFFSET`은 신규 데이터 들어오면 깨짐.

**How**:
```python
params = {
    "since": last_timestamp,     # timestamp >= since
    "after_id": last_id,         # 동일 timestamp에서 id > after_id
}
```
배치 내 `(max_ts, max_id_at_max_ts)`를 추적해 다음 cursor 산출. **모든 raw 기준으로 cursor 진전** → 중복 행만 있는 페이지에서도 무한 루프 방지.

**Code**: `ai_agent_bridge.py:162` (`_backfill_since_last`)

### 3. 멱등 UPSERT
```sql
INSERT INTO ai_agent_decisions (id, ...) VALUES (...)
ON CONFLICT (id) DO NOTHING
```

`result.rowcount == 0`이면 중복 → 집계도 skip (이중 카운트 방지).

**Code**: `ai_agent_bridge.py:301` (`_upsert_and_summarize`)

### 4. Exponential backoff (1→60s)
```python
_MIN_BACKOFF = 1.0
_MAX_BACKOFF = 60.0
backoff = min(backoff * 2, _MAX_BACKOFF)
```
- Relay 다운/네트워크 끊김 시 무한 재시도하되 부하 폭주 방지
- `stop_event`로 graceful shutdown 가능 (`asyncio.wait_for(stop_event.wait(), timeout=backoff)`)

**Code**: `ai_agent_bridge.py:144`

### 5. 정확한 평균 집계 (편향 제거)
**Why**: `avg_duration_ms`을 행별 캐시값에 가중치 `count` 곱해 평균 내면, **null-duration 행이 분모에 포함**되어 편향 발생.

**How**: `duration_sum`과 `duration_count`를 별도 컬럼으로 누적해 직접 나눔.
```sql
duration_count = COALESCE(..., 0) + CASE WHEN dur IS NULL THEN 0 ELSE 1 END
avg_duration_ms = ROUND(duration_sum::numeric / NULLIF(duration_count, 0))::int
```

**Code**: `ai_agent_bridge.py:358` (`_bump_daily`)

---

## API 응답 스펙 (요약)

### `GET /ai-agent/activity/summary?range=today|7d|30d`
```json
{
  "range": "today",
  "total": 142,
  "by_control_type": {"ventilation": 80, "irrigation": 50, "lighting": 12},
  "by_source": {"rule": 100, "llm": 42},
  "by_priority": {"high": 5, "medium": 80, "low": 57},
  "avg_duration_ms": 1240,
  "latest_at": "2026-05-03T14:32:11Z"
}
```

### `GET /ai-agent/decisions?cursor=...&limit=20`
- 최신순 keyset pagination (`(timestamp, id)` 복합 cursor)
- `has_more` + `next_cursor`/`next_cursor_id` 반환
- 필터: `control_type`, `source`, `priority`, `since`, `until`

### `GET /ai-agent/bridge/status`
```json
{
  "enabled": true,
  "healthy": true,
  "last_event_at": "2026-05-03T14:32:11Z",
  "last_backfill_at": "2026-05-03T14:00:00Z",
  "last_error": null,
  "total_processed": 14289,
  "relay_base_url": "https://relay.example.com"
}
```

---

## 운영 안전장치

| 장치 | 위치 | 효과 |
|---|---|---|
| `AI_AGENT_BRIDGE_ENABLED=False` 플래그 | `config.py` | Relay patch 미적용 환경에서 Bridge 자동 비활성 |
| `_BACKFILL_MAX_PAGES = 500` | `ai_agent_bridge.py:42` | 무한 페이지 루프 방지 (200 × 500 = 10만 건 cap) |
| 404 시 silent skip | `ai_agent_bridge.py:196` | Relay 미패치 시 INFO 로그만 남기고 종료 |
| `_SSE_READ_TIMEOUT = 120s` | `ai_agent_bridge.py:41` | heartbeat 끊김 감지 |
| Decision 단건 실패 시 rollback + 다음 이벤트 진행 | `ai_agent_bridge.py:286` | 1건 오류가 전체 스트림 멈추지 않음 |

---

## 센서 필터 (`sensor_filter.py`)

LLM과 무관한 통계 기반 필터. KY-018 조도센서의 불안정성 대응이 핵심.

```python
# 조도 0 연속 카운트 + 낮/밤 분기
if light == 0 and _is_daytime():
    if _light_zero_streak < 3:
        light = _last_valid_light  # 일시 노이즈 → 이전값 대체
        reliability = "suspicious"
    else:
        light = _last_valid_light  # 3회 이상 → 센서 장애 판정
        reliability = "unreliable"
# 야간 + 0 → 정상
```

이동평균(`deque(maxlen=10)`) 기반 급변 감지: `abs(value - avg) / avg > 0.8`이면 `suspicious`.

---

## 면접에서 강조할 포인트

1. **책임 경계 구분** — "Bridge는 ETL이지 AI가 아닙니다. AI 결정 자체는 Relay에서 합니다." → 시스템 사고 능력
2. **분산 시스템 무결성** — 이중 채널 + 멱등 + 복합 cursor = 메시지 큐 없이도 at-least-once 보장
3. **데이터 정확성** — `avg_duration_ms` 편향 제거 사례는 **"디테일을 본다"**의 좋은 예시
4. **운영 가시성** — `/bridge/status` 엔드포인트로 last_event/error/총처리수 노출

---

## 한계와 개선 가능성 (정직하게 말할 것)

- **메시지 큐(Kafka/Redis Stream)가 아니다** — 트래픽 폭증 시 Bridge 단일 프로세스 병목 가능. 향후 워커 풀 또는 Kafka 도입 검토
- **재처리 로직 없음** — DLQ(Dead Letter Queue) 미구현. 잘못된 payload는 WARN 로그만 남고 버려짐
- **Relay 의존도 100%** — Relay 장기 다운 시 데이터 공백. 향후 옵션 C(결정 엔진 FarmOS 흡수)로 보완 가능
