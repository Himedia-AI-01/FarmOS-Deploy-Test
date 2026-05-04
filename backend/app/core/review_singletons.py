"""리뷰 분석 코어 서비스 싱글턴 인스턴스.

Design Ref: §1.2 (공유 싱글턴), §13 Q3 (싱글턴 분리)
Plan SC: SC-06 (코어 단일 소스, 코드 중복 0)

기존 `api/review_analysis.py` 모듈 레벨에 있던 4개 인스턴스를
별도 모듈로 분리하여 FastAPI 라우터와 MCP tool 양쪽에서 import 가능하게 한다.
이렇게 하면 mcp/* → api/* 의 비정상적 import 방향이 사라지고,
양쪽이 동일 인스턴스(같은 ChromaDB 클라이언트, 같은 LLM client)를 공유한다.
"""

from app.core.review_analyzer import ReviewAnalyzer
from app.core.review_rag import ReviewRAG
from app.core.review_report import ReviewReportGenerator
from app.core.trend_detector import TrendDetector
from app.schemas.review_analysis import AnalysisSettings


# 서비스 인스턴스 (프로세스 싱글턴)
rag = ReviewRAG()
analyzer = ReviewAnalyzer()
trend_detector = TrendDetector()
report_generator = ReviewReportGenerator()

# 인메모리 자동 분석 설정 (추후 DB 이동 가능)
settings_state = AnalysisSettings()


__all__ = [
    "rag",
    "analyzer",
    "trend_detector",
    "report_generator",
    "settings_state",
]
