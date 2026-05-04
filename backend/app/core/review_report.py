"""리뷰 분석 PDF 리포트 생성 서비스.

# Design Ref: §3.5 — PDF 리포트 생성
# Plan SC: SC-06 (PDF 리포트 다운로드 가능)

학습 포인트:
    fpdf2는 가볍고 순수 Python으로 PDF를 생성하는 라이브러리입니다.
    (이미 pyproject.toml에 설치되어 있음)

    한글 지원:
        fpdf2는 기본적으로 한글을 지원하지 않습니다.
        TTF 폰트 파일을 등록해야 한글이 표시됩니다.
        Windows: C:/Windows/Fonts/malgun.ttf (맑은 고딕)

    PDF 구조:
        - add_page(): 새 페이지 추가
        - set_font(): 폰트 설정 (이름, 스타일, 크기)
        - cell(): 단일 줄 텍스트
        - multi_cell(): 여러 줄 텍스트 (자동 줄바꿈)
        - ln(): 줄바꿈
        - output(): PDF 바이너리 출력

사용 예시:
    from app.core.review_report import ReviewReportGenerator

    generator = ReviewReportGenerator()
    pdf_bytes = generator.generate_pdf(analysis_data)
    # → FastAPI StreamingResponse로 반환
"""

import logging
import os
from datetime import datetime
from io import BytesIO

from fpdf import FPDF
from fpdf.errors import FPDFException

from app.core.config import settings

logger = logging.getLogger(__name__)


class ReviewReportGenerator:
    """리뷰 분석 PDF 리포트 생성기.

    학습 포인트:
        FPDF를 상속하지 않고 조합(composition)으로 사용합니다.
        이렇게 하면 FPDF 내부 구현에 의존하지 않아 유지보수가 쉽습니다.
    """

    FONT_NAME = "MalgunGothic"

    def generate_pdf(self, analysis_data: dict) -> BytesIO:
        """분석 결과를 PDF 리포트로 생성합니다.

        포함 내용:
        1. 리포트 제목 및 생성 일시
        2. 감성 분석 요약 (긍정/부정/중립 비율)
        3. 주요 키워드 (상위 10개)
        4. AI 인사이트 (요약 및 제안)
        5. 이상 탐지 알림 (있을 경우)

        Args:
            analysis_data: 분석 결과 딕셔너리
                {
                    sentiment_summary: { positive, negative, neutral, total },
                    keywords: [{ word, count, sentiment }],
                    summary: { overall, positives, negatives, suggestions },
                    anomalies: [{ week, type, message }],  # optional
                    processing_time_ms: int,
                    llm_provider: str,
                }

        Returns:
            PDF 바이너리 데이터 (BytesIO)
        """
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)

        # 한글 폰트 등록
        self._register_font(pdf)

        pdf.add_page()

        # 1. 제목
        self._add_title(pdf)

        # 2. 감성 분석 요약
        self._add_sentiment_summary(pdf, analysis_data.get("sentiment_summary", {}))

        # 3. 주요 키워드
        self._add_keywords(pdf, analysis_data.get("keywords", []))

        # 4. AI 인사이트
        self._add_summary(pdf, analysis_data.get("summary", {}))

        # 5. 이상 탐지 알림
        anomalies = analysis_data.get("anomalies", [])
        if anomalies:
            self._add_anomalies(pdf, anomalies)

        # 6. 메타 정보
        self._add_meta(pdf, analysis_data)

        # PDF 출력
        output = BytesIO()
        pdf.output(output)
        output.seek(0)
        return output

    # ------------------------------------------------------------------
    # 폰트 등록
    # ------------------------------------------------------------------

    # OS 별 한글 폰트 자동 탐색 후보 — (regular, bold) 쌍.
    # bold 가 None 이면 regular 를 양쪽으로 등록한다 (fpdf2 가 동일 파일로 bold 시뮬).
    _FALLBACK_FONT_CANDIDATES: list[tuple[str, str | None]] = [
        # Windows — Malgun Gothic
        ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf"),
        ("C:/Windows/Fonts/gulim.ttc", None),
        # macOS — Apple SD Gothic Neo
        ("/System/Library/Fonts/AppleSDGothicNeo.ttc", None),
        ("/Library/Fonts/AppleGothic.ttf", None),
        # Linux — Nanum/Noto
        ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
         "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ]

    def _resolve_font_paths(self) -> tuple[str, str | None] | None:
        """settings.FONT_PATH > OS 별 후보 순으로 한글 폰트 경로를 찾는다.

        Returns:
            (regular, bold|None) 튜플 또는 None (어떤 폰트도 못 찾음).
        """
        configured = settings.FONT_PATH
        if configured and os.path.exists(configured):
            return (configured, None)

        for regular, bold in self._FALLBACK_FONT_CANDIDATES:
            if os.path.exists(regular):
                bold_path = bold if (bold and os.path.exists(bold)) else None
                return (regular, bold_path)

        return None

    def _register_font(self, pdf: FPDF):
        """한글 폰트를 등록한다.

        탐색 우선순위:
            1. settings.FONT_PATH (사용자 명시)
            2. OS 표준 한글 폰트 경로 (Windows Malgun, macOS Apple SD Gothic, Linux Nanum/Noto)
            3. 모두 실패 시 경고만 — _set_font 가 Helvetica 로 폴백하지만
               한글 텍스트는 FPDFUnicodeEncodingException 을 일으킨다.
        """
        resolved = self._resolve_font_paths()
        if resolved is None:
            logger.warning(
                "한글 폰트를 찾을 수 없음 (settings.FONT_PATH 미지정 + OS 표준 경로 부재). "
                "Helvetica 폴백 — 한글 텍스트는 깨질 수 있다."
            )
            return

        regular_path, bold_path = resolved
        pdf.add_font(self.FONT_NAME, "", regular_path, uni=True)
        # bold 별도 파일이 있으면 그걸로, 없으면 regular 를 bold 자리에 등록 (fpdf2 시뮬레이트)
        pdf.add_font(self.FONT_NAME, "B", bold_path or regular_path, uni=True)
        logger.info(
            "한글 폰트 등록: regular=%s bold=%s",
            regular_path, bold_path or "(same as regular)",
        )

    def _set_font(self, pdf: FPDF, style: str = "", size: int = 10):
        """폰트를 설정합니다 (한글 폰트 우선, 없으면 Helvetica).

        한글 폰트가 등록되지 않았거나 (style 변형이 없는) 경우 fpdf2 가
        FPDFException 을 던진다. 양쪽을 모두 잡아 Helvetica 로 폴백한다.
        """
        try:
            pdf.set_font(self.FONT_NAME, style, size)
        except (RuntimeError, FPDFException):
            pdf.set_font("Helvetica", style, size)

    # ------------------------------------------------------------------
    # 섹션 렌더링
    # ------------------------------------------------------------------

    def _add_title(self, pdf: FPDF):
        """리포트 제목을 추가합니다."""
        self._set_font(pdf, "B", 18)
        pdf.cell(0, 12, "FarmOS Review Analysis Report", ln=True, align="C")
        pdf.ln(2)

        self._set_font(pdf, "", 10)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        pdf.cell(0, 6, f"Generated: {now}", ln=True, align="C")
        pdf.ln(8)

        # 구분선
        pdf.set_draw_color(200, 200, 200)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(6)

    def _add_sentiment_summary(self, pdf: FPDF, summary: dict):
        """감성 분석 요약 섹션."""
        self._set_font(pdf, "B", 14)
        pdf.cell(0, 10, "1. Sentiment Summary", ln=True)
        pdf.ln(2)

        total = summary.get("total", 0)
        positive = summary.get("positive", 0)
        negative = summary.get("negative", 0)
        neutral = summary.get("neutral", 0)

        # 테이블 헤더
        self._set_font(pdf, "B", 10)
        col_w = 45
        pdf.cell(col_w, 8, "Category", 1, 0, "C")
        pdf.cell(col_w, 8, "Count", 1, 0, "C")
        pdf.cell(col_w, 8, "Ratio", 1, 0, "C")
        pdf.cell(col_w, 8, "Bar", 1, 1, "C")

        # 데이터 행
        self._set_font(pdf, "", 10)
        rows = [
            ("Positive", positive, total),
            ("Negative", negative, total),
            ("Neutral", neutral, total),
        ]

        for label, count, t in rows:
            ratio = count / t if t > 0 else 0
            bar = "#" * int(ratio * 20)
            pdf.cell(col_w, 7, label, 1, 0, "C")
            pdf.cell(col_w, 7, str(count), 1, 0, "C")
            pdf.cell(col_w, 7, f"{ratio:.0%}", 1, 0, "C")
            pdf.cell(col_w, 7, bar, 1, 1, "L")

        self._set_font(pdf, "B", 10)
        pdf.cell(col_w, 7, "Total", 1, 0, "C")
        pdf.cell(col_w, 7, str(total), 1, 0, "C")
        pdf.cell(col_w, 7, "100%", 1, 0, "C")
        pdf.cell(col_w, 7, "", 1, 1, "C")
        pdf.ln(6)

    def _add_keywords(self, pdf: FPDF, keywords: list[dict]):
        """주요 키워드 섹션 (상위 10개)."""
        self._set_font(pdf, "B", 14)
        pdf.cell(0, 10, "2. Top Keywords", ln=True)
        pdf.ln(2)

        if not keywords:
            self._set_font(pdf, "", 10)
            pdf.cell(0, 7, "No keywords extracted.", ln=True)
            pdf.ln(4)
            return

        # 테이블 헤더
        self._set_font(pdf, "B", 10)
        pdf.cell(20, 8, "#", 1, 0, "C")
        pdf.cell(60, 8, "Keyword", 1, 0, "C")
        pdf.cell(30, 8, "Count", 1, 0, "C")
        pdf.cell(40, 8, "Sentiment", 1, 0, "C")
        pdf.cell(40, 8, "Bar", 1, 1, "C")

        # 데이터 (상위 10개)
        self._set_font(pdf, "", 10)
        max_count = keywords[0]["count"] if keywords else 1
        for i, kw in enumerate(keywords[:10], 1):
            bar_len = int(kw["count"] / max_count * 15)
            bar = "#" * bar_len
            pdf.cell(20, 7, str(i), 1, 0, "C")
            pdf.cell(60, 7, kw["word"], 1, 0, "L")
            pdf.cell(30, 7, str(kw["count"]), 1, 0, "C")
            pdf.cell(40, 7, kw["sentiment"], 1, 0, "C")
            pdf.cell(40, 7, bar, 1, 1, "L")

        pdf.ln(6)

    def _add_summary(self, pdf: FPDF, summary: dict):
        """AI 인사이트 섹션."""
        self._set_font(pdf, "B", 14)
        pdf.cell(0, 10, "3. AI Insights", ln=True)
        pdf.ln(2)

        if not summary:
            self._set_font(pdf, "", 10)
            pdf.cell(0, 7, "No summary available.", ln=True)
            pdf.ln(4)
            return

        # 전체 요약
        overall = summary.get("overall", "")
        if overall:
            self._set_font(pdf, "B", 11)
            pdf.cell(0, 7, "Overall:", ln=True)
            self._set_font(pdf, "", 10)
            pdf.multi_cell(0, 6, overall)
            pdf.ln(3)

        # 긍정 포인트
        positives = summary.get("positives", [])
        if positives:
            self._set_font(pdf, "B", 11)
            pdf.cell(0, 7, "Positive Points:", ln=True)
            self._set_font(pdf, "", 10)
            for p in positives:
                pdf.cell(5, 6, "")
                pdf.cell(0, 6, f"+ {p}", ln=True)
            pdf.ln(2)

        # 부정 포인트
        negatives = summary.get("negatives", [])
        if negatives:
            self._set_font(pdf, "B", 11)
            pdf.cell(0, 7, "Negative Points:", ln=True)
            self._set_font(pdf, "", 10)
            for n in negatives:
                pdf.cell(5, 6, "")
                pdf.cell(0, 6, f"- {n}", ln=True)
            pdf.ln(2)

        # 개선 제안
        suggestions = summary.get("suggestions", [])
        if suggestions:
            self._set_font(pdf, "B", 11)
            pdf.cell(0, 7, "Suggestions:", ln=True)
            self._set_font(pdf, "", 10)
            for i, s in enumerate(suggestions, 1):
                pdf.cell(5, 6, "")
                pdf.cell(0, 6, f"{i}. {s}", ln=True)

        pdf.ln(4)

    def _add_anomalies(self, pdf: FPDF, anomalies: list[dict]):
        """이상 탐지 알림 섹션."""
        self._set_font(pdf, "B", 14)
        pdf.cell(0, 10, "4. Anomaly Alerts", ln=True)
        pdf.ln(2)

        self._set_font(pdf, "", 10)
        for a in anomalies:
            pdf.set_fill_color(255, 235, 235)
            pdf.cell(5, 7, "", 0, 0)
            pdf.cell(0, 7, f"[{a.get('week', '')}] {a.get('message', '')}", 0, 1, fill=True)

        pdf.ln(4)

    def _add_meta(self, pdf: FPDF, data: dict):
        """메타 정보 (처리 시간, LLM 제공자 등)."""
        pdf.ln(4)
        pdf.set_draw_color(200, 200, 200)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(4)

        self._set_font(pdf, "", 8)
        pdf.set_text_color(128, 128, 128)

        processing_ms = data.get("processing_time_ms", 0)
        provider = data.get("llm_provider", "unknown")
        model = data.get("llm_model", "unknown")

        pdf.cell(0, 5, f"Processing Time: {processing_ms}ms", ln=True)
        pdf.cell(0, 5, f"LLM Provider: {provider} ({model})", ln=True)
        pdf.cell(0, 5, f"Generated by FarmOS Review Analysis System", ln=True)

        pdf.set_text_color(0, 0, 0)  # 색상 복원
