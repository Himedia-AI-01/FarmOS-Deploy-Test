# Vision 모델 선정 + 파인튜닝 미채택 결정 기록

> **Feature**: journal-vision-input
> **Date**: 2026-04-28
> **Author**: JunePark2018
> **Status**: Decided
> **Related**: [Plan](./../../01-plan/features/journal-vision-input.plan.md) · [Design](./journal-vision-input.design.md) · [Analysis](./../../03-analysis/journal-vision-input.analysis.md)

영농일지 사진 입력 기능에서 어떤 vision 모델을 어떻게 쓸지 검토한 의사결정을 기록한다. 보고/리뷰 시 근거 자료로 사용.

---

## 1. Context

멘토(2026-04-28) 제안: "사진 N장 → AI 가 영농일지 자동 작성." 사진 → 농업ON 포맷 entry 11개 필드(work_date, field_name, crop, work_stage, weather, disease, usage_pesticide_*, usage_fertilizer_*, detail) prefill 이 핵심 task.

요구사항:
- 한국어 농약 라벨 OCR (이미지 안의 한국어 글자 인식)
- 작업단계 추론 (사진 보고 사전준비/경운/파종/정식/작물관리/수확 분류)
- 멀티 이미지 그룹핑 (같은 작업 N장 → 1 entry, 다른 작업 → N entry)
- 환각 방지 (모르면 null, 임의 농약명 X)
- 응답 ≤ 8s 가 이상적 (실측 11~16s 허용 범위로 완화)

---

## 2. 검토한 옵션

### 2.1 모델 선택지

| 옵션 | Vision capable | 한국어 OCR | 멀티이미지 | 비용 | 라이선스/배포 | 평가 |
|------|:---:|:---:|:---:|:---:|---|---|
| **A. Gemini 2.5 Flash via LiteLLM 프록시** | O | 강함 | O | 매우 저렴 ($0.075/1M in) | LiteLLM 프록시 등록 필요 | **목표였으나 프록시 미등록** |
| **B. GPT-5 mini via LiteLLM 프록시** | O | 양호 | O | 저렴 | 프록시 등록됨 | **채택** |
| **C. GPT-5 nano via LiteLLM 프록시** | O | 양호 | O | 더 저렴 | 프록시 등록됨 | mini 보다 품질 낮을 가능성, 비용 차 미세 |
| **D. Gemma 4 31B-IT via LiteLLM 프록시** | X (text only) | — | — | 저렴 | 프록시 등록됨 | vision 불가, 제외 |
| **E. Claude Sonnet 4.6 via Anthropic API 직호출** | O | 매우 강함 | O | 중간 ($3/1M in) | 별도 키 발급 필요 | 비용 ↑, 키 추가 필요 → V1 부적합 |
| **F. OpenRouter Gemini Flash 직호출** | O | 강함 | O | 저렴 | 별도 키 발급 필요 | LiteLLM 우회 — V1.1 fallback |
| **G. Qwen2-VL 7B 자체 호스팅** | O | 약함 (한국어) | O | GPU 호스팅 비용 | 직접 배포 | 운영 부담 큼 |

### 2.2 fine-tuning 여부

| 접근 | 필요 데이터 | 인프라 | 시간 | 한계 |
|------|---|---|---|---|
| Zero-shot prompt engineering | 0 | LLM API | 1~2일 | 환각 가능성, 농약명 OCR 정확도 한계 |
| Few-shot in-context | ~10건 예시 | LLM API | 2~3일 | 토큰 비용 ↑, prompt 비대 |
| LoRA 파인튜닝 (open-weights vision LM) | ~수천 장 | GPU 24GB+ | 2~4주 | §4 참조 |
| Full fine-tune (commercial API) | 수만 장 | OpenAI/Google FT 서비스 | 수 주 | 비용·데이터 노출 위험 |

---

## 3. 채택 결정

### 3.1 모델: **GPT-5 mini via LiteLLM 프록시**

근거:
- (즉시성) 2026-04-28 LiteLLM 프록시(`litellm.lilpa.moe/v1`) 의 `/models` 점검 결과 vision-capable 모델은 `gpt-5-mini`, `gpt-5-nano` 둘. **그날 동작하는 가장 안전한 default**.
- (호환성) OpenAI 호환 chat/completions, `content` 에 `image_url` 배열 표준 → 별도 SDK 추가 없음, 기존 `httpx` 호출 패턴 유지.
- (전환 자유도) `LITELLM_VISION_MODEL` 환경변수로 추상화 — 프록시에 Gemini Flash 등록 시 `.env` 한 줄로 교체.
- (응답 품질) 합성 라벨 라이브 테스트(`프로피네브 수화제 / 500배액 / 사과 탄저병`) 에서 OCR + 농약 DB 매칭 confidence 0.95.
- (비용) 사진 1건 처리 ≈ $0.0002 추정 (가성비 충분).

### 3.2 Fine-tuning: **미채택 (zero-shot prompt engineering 채택)**

zero-shot 채택 근거:
- (a) 라이브 테스트에서 GPT-5 mini 가 한국어 라벨 OCR + work_stage 추론 + 농약 매칭을 모두 정상 수행
- (b) 농약명은 별도 후처리(`enrich_with_pesticide_match` rapidfuzz 매칭) 로 환각 방지 — fine-tune 안 해도 비매칭 시 raw 보존
- (c) 사용자 검수(미리보기 폼 prefill) 단계가 안전망 — LLM 출력은 100% 정확할 필요 없음
- (d) ROI: §4 의 비용 vs §3.1 옵션의 즉시 가용성 비교 시 zero-shot 우위 명확

V3 재평가 조건: 시연/실 사용자 데이터 누적 (최소 100건 이상 + 라벨링) + GPT-5 mini baseline 의 정량 한계가 측정된 시점.

---

## 4. 파인튜닝을 시도했다면 부딪혔을 장애물

> 본 V1 에서 fine-tuning 은 채택하지 않았으나, 옵션으로 검토는 했다. 구상이 실제로 진행됐을 경우 마주칠 가능성 높은 장애 요소를 산업 경험 기반으로 정리한다. 보고 시 "구상 → 분석 → 미채택" 의사결정 흐름 근거.

### 4.1 데이터 수집 — 가장 큰 장벽

- **한국 농업 vision 공개 데이터셋이 사실상 없음.** AI Hub 의 "병해충 이미지" 류는 잎 클로즈업 위주이지 농약 통 라벨/필지 전경/작업 도구는 거의 미수록.
- 멘토가 요구하는 task 다양성:
  - 농약/비료 라벨 OCR (수십~수백 가지 제품)
  - 작목 분류 (사과/배/포도/감/딸기/고추/토마토/벼…)
  - 작업단계 분류 (방제/수확/정식/경운…) — 이게 일반 image classification 데이터셋에 거의 없는 카테고리
  - 병해충 인식 (잎 변색/반점/해충 자체)
- 직접 수집 시: 농가 동행 → 동의 + 촬영 + 라벨링. **현실적으로 100~500장이 상한**, 수천 장 모으려면 산학 협력 필요.
- 농약 통은 저작권·상표 issue (제품명 노출). 농가 전경은 개인정보 (배경에 차량 번호판/얼굴/주소판).

### 4.2 라벨링 비용·복잡도

- 단일 task 가 아니라 **multi-task multi-label** (사진 1장에 OCR 텍스트 + 작목 + 작업단계 + 병해충 + 농약 매칭 + EXIF 시간 검증).
- 라벨링 1장당 평균 3~5분 추정 → 1,000장 = 60시간 이상 (비전공자 기준). 라벨러간 일관성 검증 (inter-rater reliability) 별도 작업.
- 농약 정확 표기 정규화 (예: "프로피네브 수화제" vs "프로피네브 75% 수화제" vs "프로피네브") — 도메인 전문가 의존도 높음.

### 4.3 모델 선택 + 인프라

- 후보 base 모델:
  - **Qwen2-VL 7B / LLaVA 1.6 7B**: open-weights, LoRA 가능. 한국어 OCR 약함 — 추가 한국어 적응 fine-tune 필요해 데이터 더 필요.
  - **PaliGemma 3B**: 작고 빠르나 한국어 거의 못 함.
  - **OpenAI/Google fine-tune API**: 데이터 외부 전송 필요, 라이선스/개인정보 검토.
- LoRA 파인튜닝 최소 GPU: 24GB VRAM (Qwen2-VL 7B 기준 8bit + LoRA 어댑터). RTX 4090 / A6000 / A100. 클라우드 시간당 $0.5~$2 × 24~48시간 = $30~$100 + 시행착오 3~5회 = 실제 $100~$500.
- 파인튜닝된 모델 추론 호스팅 비용도 별도 (vLLM 서빙 + GPU 상시 가동).

### 4.4 검증·평가의 모호함

- "잘된 fine-tune" 의 기준이 task 별로 다름. 평가 셋 100~200건 + 라벨러 합의 + 정량 metric (필드 정확도, OCR 정확도, 환각률) + 사람 평가.
- baseline (zero-shot GPT-5 mini) 가 이미 양호하므로 **유의미한 개선** 을 만들기 위한 데이터 양이 의외로 큼. 학계 보고들에서도 "수백 장으로 LoRA 한 모델 < 수십억 token 으로 학습된 base 모델 + 좋은 prompt" 인 경우가 빈번.

### 4.5 도메인 표류 (Domain Drift)

- 농약 제품 갱신 주기: 매년 신규 출시 + 등록 취소. fine-tuned 모델은 학습 시점 정보로 고정 → **3~6개월마다 재학습 부담**.
- prompt + DB 매칭 방식 (현 V1) 은 농약 DB 만 갱신하면 곧바로 반영 — 유지보수 비용 차이 큼.

### 4.6 시간 vs 시연 일정

- 멘토 시연까지 가용 시간: 몇 일~한 주.
- 데이터 수집(2주) + 라벨링(1주) + 실험(1~2주) + 평가(3일) = **최소 4~6주.** 시연 일정과 절대 못 맞음.
- 즉시 가용한 baseline (GPT-5 mini zero-shot + 농약 DB 후처리) 으로 시연 → 데이터 누적 → V3 재평가가 자연스러운 단계 분리.

### 4.7 결론적 trade-off

| 축 | Zero-shot prompt | LoRA Fine-tuning |
|---|---|---|
| 시간 | 1~2일 | 4~6주 |
| 비용 | API ~$1/일 | GPU $100~$500 + 데이터 수집 |
| 위험 | 환각 (사용자 검수로 흡수) | 데이터 부족 → baseline 못 이김 |
| 유지비 | 0 (DB 만 갱신) | 3~6개월 재학습 |
| 시연 임팩트 | "AI 가 사진 보고 자동 채움" 즉시 시연 가능 | 시연 못 맞춤 |

→ **명확한 zero-shot 우위.** Fine-tuning 은 데이터 누적 후 재평가할 V3 의 의제로 유지.

---

## 5. 사용자 검수가 안전망인 이유

LLM 응답이 100% 정확할 필요가 없는 이유는 **사용자가 폼에서 prefill 결과를 검수·편집한 뒤 저장하는 흐름** 이기 때문. LLM 은 "초안 작성 비서" 역할이고 최종 책임은 사람.

이 안전망 덕분에:
- 정밀 fine-tune 보다 빠른 prompt + DB 매칭이 합리
- 거절 응답에도 "그래도 직접 작성" 옵션으로 LLM 의 false negative 흡수 (재현율 100% 아님 인정)
- 환각이 100% 차단되지 않아도 시스템이 안전하게 동작

---

## 6. 향후 재평가 트리거

다음 중 하나 만족 시 fine-tuning 검토 재개:

1. 실 사용자 누적 데이터 ≥ 1,000건 (라벨된 사진 + 사용자가 최종 저장한 entry pair)
2. GPT-5 mini baseline 의 정량 한계 측정 (예: 농약 OCR 정확도 < 70%, 환각률 > 15% 등)
3. LiteLLM 프록시에 Gemini 2.5 Flash + Claude Sonnet 4.6 등 모두 등록되었으나 baseline 으로도 부족한 경우
4. 비용 폭증 (월 호출 비용 > $50)

---

## 7. 즉시 적용 가능한 V1.1 개선 (fine-tune 없이)

- 농약 후보 hint 강화: 사용자 crop/지역 컨텍스트 기반 후보 랭킹 (이미 부분 구현)
- few-shot 예시 추가: 시스템 prompt 에 좋은 사례 2~3개 (input 사진 설명 + ideal output JSON)
- 사진 다운샘플 해상도 상향 검토 (1280px → 1600px) — 한글 라벨 OCR 정확도 ↑ 가능
- 응답 시간: Gemini Flash 등록 후 환경변수 전환 (5~7s 예상)
- EXIF strip 옵션 (V2): 위치정보 노출 우려 시 사용자 선택권

---

## 8. 보고용 한 줄 요약

> "Vision 모델은 LiteLLM 프록시 등록 현황 점검 후 GPT-5 mini 채택. Fine-tuning 은 검토 결과 데이터 수집/라벨링/GPU 비용 vs zero-shot baseline 의 즉시 가용성을 비교해 V1 미채택, 실데이터 누적 후 V3 재평가로 단계 분리."

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1.0 | 2026-04-28 | Initial — 모델 선정 + fine-tuning 미채택 의사결정 기록 + 가정 분석 | JunePark2018 |
