# Interview & Portfolio Materials

> FarmOS-v2 프로젝트의 **IoT 적재 파이프라인** 및 **리뷰 자동화 분석** 모듈에 대한 면접/발표용 정리.
> 발표일: **2026-05-11** · 담당자: clover0309

---

## 핵심 메시지 (1줄)

> "팀의 다른 모듈은 LangChain 기반 AI Agent로 구현됐지만, 제 모듈은 **MCP(Model Context Protocol) 표준**으로 노출하여
> LangChain·Claude Desktop·Cursor 어떤 클라이언트에서도 호출 가능한 **한 층 위 표준 인터페이스**를 만들었습니다."

---

## 문서 구성

| 파일 | 용도 | 누구에게 보여줄지 |
|---|---|---|
| [01-tech-decision-mcp-vs-langchain.md](./01-tech-decision-mcp-vs-langchain.md) | LangChain 미도입의 의식적 의사결정 정리 | 면접관 (가장 자주 받는 질문) |
| [02-iot-bridge-implementation.md](./02-iot-bridge-implementation.md) | IoT Bridge 구조 (SSE+HTTP 이중채널, 멱등 UPSERT, 집계) | 인프라/백엔드 면접관 |
| [03-review-automation-implementation.md](./03-review-automation-implementation.md) | 리뷰 RAG/LLM 분석/트렌드/PDF/MCP 노출 구조 | AI/풀스택 면접관 |
| [04-interview-qna.md](./04-interview-qna.md) | 예상 질문 14개 + 30초/2분 답변 + follow-up | 면접 직전 리허설 |

---

## 사용법

### 면접 전날
1. `04-interview-qna.md` 읽고 30초 답변을 입으로 3번씩 연습
2. `01-tech-decision-mcp-vs-langchain.md` 결정 근거 4개를 외울 것 (외운 티 안 나게)
3. 시연 데모 영상을 **반드시 백업**으로 준비 (라이브 데모 실패 대비)

### 면접 중
- LangChain 질문 → `01` 문서의 핵심 4가지 근거 중 1~2개로 답
- "어떻게 구현했어요?" → `02` 또는 `03`의 다이어그램을 머릿속에 그리며 데이터 흐름 순으로 설명
- 모르는 질문 → "그건 검토해보지 못했지만, 비슷한 결정을 할 때는 ~를 기준으로 합니다" (트레이드오프 사고를 보여줄 것)

### 포트폴리오 PDF/노션 작성 시
- `01` 문서의 "결론·대안·근거·계획" 4단 구조를 그대로 차용
- `02`/`03` 문서의 **다이어그램**을 그림으로 다시 그려 첨부 (Mermaid → 이미지)
- 코드 인용은 GitHub 영구 링크(`?raw=...`)로 걸어 둘 것

---

## 절대 하지 말 것

- ❌ "LangChain 잘 몰라서요" — 즉시 약점
- ❌ "팀이 쓰니까 저도 곧 따라가려고요" — 주체성 부재
- ❌ LangChain 자체를 비난 — 옹졸해 보임. **항상 "내 컨텍스트에선" 한정** 사용
- ❌ 발표 직전(D-3 이후) 코드 변경 — 시연 실패 1순위 원인

---

## 참조

- 마감/우선순위: `../memory/project_farmos_v2.md`
- 의사결정 톤: `../memory/feedback_langchain_decision.md`
- 기술 스택 스냅샷: `../memory/project_tech_stack.md`
