"""프로젝트 경로 상수 모음.

모든 경로는 이 모듈에서 중앙 관리합니다.
다른 모듈에서 os.path / __file__ 기반 경로를 직접 계산하지 마세요.
"""
import os
from pathlib import Path

# shopping_mall/backend/app/
APP_DIR = Path(__file__).parent

# shopping_mall/backend/
BACKEND_ROOT = APP_DIR.parent

# shopping_mall/backend/ai/
AI_DIR = BACKEND_ROOT / "ai"

# FarmOS/  (프로젝트 최상위)
# 호스트 레이아웃: FarmOS/shopping_mall/backend → parent.parent = FarmOS
# 도커 레이아웃:   /app/app → BACKEND_ROOT=/app, parent.parent=/ (루트)
#                 → 컨테이너에서는 /logs 가 되어 권한 에러 발생.
# 따라서 LOG_DIR 은 환경변수로 override 가능하게 두고,
# 컨테이너에서는 docker-compose 가 LOG_DIR=/app/logs 주입.
PROJECT_ROOT = BACKEND_ROOT.parent.parent

# 로그 디렉토리 — 환경변수 LOG_DIR 우선
#   - 로컬 개발: 미설정 → FarmOS/logs/ (개발자 권한)
#   - 컨테이너:  LOG_DIR=/app/logs (Dockerfile 의 chown -R appuser:appgroup /app 으로 권한 보장)
LOG_DIR = Path(os.environ.get("LOG_DIR") or str(PROJECT_ROOT / "logs"))

# shopping_mall/backend/chroma_data/
CHROMA_DB_PATH = str(BACKEND_ROOT / "chroma_data")

# shopping_mall/backend/ai/data/
AI_DATA_DIR = AI_DIR / "data"
