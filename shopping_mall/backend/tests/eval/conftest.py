"""tests/eval/ 전용 conftest.

Windows + PyTorch 환경에서 발생하는 OpenMP 충돌 및 access violation을
방지하기 위해 환경변수를 pytest 수집 시점보다 먼저 설정합니다.

설정 근거:
  - KMP_DUPLICATE_LIB_OK=TRUE : OpenMP DLL 중복 로딩 허용
  - OMP_NUM_THREADS=1         : 단일 스레드 강제 (멀티스레드 충돌 방지)
  - TOKENIZERS_PARALLELISM=false : HuggingFace tokenizer 병렬화 비활성화
"""
import os
import sys

if sys.platform.startswith("win"):
    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
