#!/usr/bin/env bash
# Design Ref: §16.9 — farmos-ec2-deploy Module 4-B (v0.4)
# Plan SC: SC-9 (Parameter Store → .env 자동 생성), R10/R13 완화
#
# AfterInstall 훅:
#   1) release/ → /opt/farmos/ 배포 산출물 동기화
#   2) EC2 region 자동 감지 (IMDSv2)
#   3) Parameter Store /farmos/prod/* 28키 조회 → 명시적 매핑으로 .env 생성
#   4) 비-비밀 default 추가
#   5) 권한 잠금 (chmod 600)
#   6) 28키 + 6핵심 변수 사전 검증 (R13 완화)
#   7) .env syntax check (R13 추가 안전장치)

set -euo pipefail
IFS=$'\n\t'

LOG() { echo "[after-install] $(date -Iseconds) $*"; }
ERROR() { echo "[after-install] $(date -Iseconds) ERROR: $*" >&2; }

# ────────────────────────────────────────────────
# 1) release 디렉토리 → /opt/farmos 동기화
# ────────────────────────────────────────────────
LOG "Syncing release files to /opt/farmos"
cp -f /opt/farmos/release/docker-compose.yml /opt/farmos/docker-compose.yml
cp -f /opt/farmos/release/nginx.conf         /opt/farmos/nginx.conf 2>/dev/null \
  || cp -f /opt/farmos/release/nginx/nginx.conf /opt/farmos/nginx.conf
if [ -d /opt/farmos/release/dist ]; then
  rsync -a --delete /opt/farmos/release/dist/ /opt/farmos/dist/
fi
# shopping_mall frontend dist (신규)
if [ -d /opt/farmos/release/shop-dist ]; then
  mkdir -p /opt/farmos/shop-dist
  rsync -a --delete /opt/farmos/release/shop-dist/ /opt/farmos/shop-dist/
fi

# ────────────────────────────────────────────────
# 2) EC2 region 자동 감지 (IMDSv2)
# ────────────────────────────────────────────────
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
REGION=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/placement/region)
if [ -z "$REGION" ]; then
  ERROR "Failed to detect EC2 region via IMDSv2"
  exit 1
fi
LOG "Detected region: $REGION"

# ────────────────────────────────────────────────
# 3) Parameter Store 조회 → 명시적 매핑 → .env
# ────────────────────────────────────────────────
LOG "Fetching parameters from /farmos/prod"
ENV_FILE=/opt/farmos/.env
TMP_JSON=$(mktemp)
trap 'rm -f "$TMP_JSON"' EXIT

aws ssm get-parameters-by-path \
  --path /farmos/prod \
  --recursive \
  --with-decryption \
  --region "$REGION" \
  --output json \
  > "$TMP_JSON"

# /farmos/prod/shop/* 키만 따로 추출 — shop-api 컨테이너 전용 .env.shop 생성용
TMP_SHOP_JSON=$(mktemp)
trap 'rm -f "$TMP_JSON" "$TMP_SHOP_JSON"' EXIT
jq '{Parameters: [.Parameters[] | select(.Name | startswith("/farmos/prod/shop/"))]}' \
  < "$TMP_JSON" > "$TMP_SHOP_JSON"
SHOP_PARAM_COUNT=$(jq '.Parameters | length' < "$TMP_SHOP_JSON")
LOG "Shop-specific parameters: $SHOP_PARAM_COUNT"

# R10 추가 안전장치: 빈 응답(권한 누락 또는 시드 미완료) 즉시 검출
PARAM_COUNT=$(jq '.Parameters | length' < "$TMP_JSON")
if [ "$PARAM_COUNT" -eq 0 ]; then
  ERROR "Parameter Store returned 0 keys. Check IAM Instance Profile permissions (ssm:GetParametersByPath on /farmos/prod/*)"
  exit 1
fi
LOG "Fetched $PARAM_COUNT parameters"

# SSM path → config.py 변수명 명시 매핑 (jq lookup 테이블)
jq -r '
    def name_map(n):
      if   n == "/farmos/prod/db/url"                       then "DATABASE_URL"
      elif n == "/farmos/prod/db/password"                  then "POSTGRES_PASSWORD"
      elif n == "/farmos/prod/jwt/secret_key"               then "JWT_SECRET_KEY"
      elif n == "/farmos/prod/cors/origins"                 then "CORS_ORIGINS"
      elif n == "/farmos/prod/litellm/url"                  then "LITELLM_URL"
      elif n == "/farmos/prod/litellm/api_key"              then "LITELLM_API_KEY"
      elif n == "/farmos/prod/litellm/model"                then "LITELLM_MODEL"
      elif n == "/farmos/prod/llm/upstage_key"              then "UPSTAGE_API_KEY"
      elif n == "/farmos/prod/llm/reasoning_effort"         then "LLM_REASONING_EFFORT"
      elif n == "/farmos/prod/llm/provider"                 then "LLM_PROVIDER"
      elif n == "/farmos/prod/llm/model"                    then "LLM_MODEL"
      elif n == "/farmos/prod/llm/embed_model"              then "EMBED_MODEL"
      elif n == "/farmos/prod/llm/ai_agent_model"           then "AI_AGENT_MODEL"
      elif n == "/farmos/prod/groq/api_key"                 then "GROQ_API_KEY"
      elif n == "/farmos/prod/groq/stt_url"                 then "GROQ_STT_URL"
      elif n == "/farmos/prod/groq/stt_model"               then "GROQ_STT_MODEL"
      elif n == "/farmos/prod/iot_relay/base_url"           then "IOT_RELAY_BASE_URL"
      elif n == "/farmos/prod/iot_relay/api_key"            then "IOT_RELAY_API_KEY"
      elif n == "/farmos/prod/iot_relay/bridge_enabled"     then "AI_AGENT_BRIDGE_ENABLED"
      elif n == "/farmos/prod/external/kma_decoding_key"    then "KMA_DECODING_KEY"
      elif n == "/farmos/prod/external/ncpms_key"           then "NCPMS_API_KEY"
      elif n == "/farmos/prod/external/pesticide_key"       then "PESTICIDE_API_KEY"
      elif n == "/farmos/prod/external/food_safety_key"     then "FOOD_SAFETY_API_KEY"
      elif n == "/farmos/prod/external/kamis_key"           then "KAMIS_API_KEY"
      elif n == "/farmos/prod/external/kamis_cert_id"       then "KAMIS_CERT_ID"
      elif n == "/farmos/prod/external/kakao_rest_key"      then "KAKAO_REST_API_KEY"
      elif n == "/farmos/prod/image/tag"                    then "IMAGE_TAG"
      elif n == "/farmos/prod/ghcr/owner"                   then "GHCR_OWNER"
      elif n == "/farmos/prod/seed/dump_url"                then "SEED_DUMP_S3_URL"
      else
        n | sub("/farmos/prod/"; "") | gsub("/"; "_") | ascii_upcase
      end;
    .Parameters[]
    # shop/* 키는 .env.shop 으로 따로 생성됨 — main .env 에서 제외 (이중 노출 방지)
    | select(.Name | startswith("/farmos/prod/shop/") | not)
    # @sh: 값에 따옴표/스페이스/쉘 메타문자가 있어도 안전하게 single-quote wrap
    # bash source 와 docker compose env_file 둘 다 single-quote stripping 지원
    | "\(name_map(.Name))=\(.Value | @sh)"
  ' < "$TMP_JSON" > "$ENV_FILE"

# ────────────────────────────────────────────────
# 4) 비-비밀 default 추가 (config.py 가 default를 갖지만 명시적으로 박는 게 운영 가시성 좋음)
# ────────────────────────────────────────────────
cat >> "$ENV_FILE" <<'DEFAULTS'
PROJECT_NAME=FarmOS
API_V1_PREFIX=/api/v1
APP_TIMEZONE=Asia/Seoul
POSTGRES_USER=farmos
POSTGRES_DB=farmos
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=1800
CHROMA_DB_PATH=/app/chroma_data
EMBED_DIM=1024
REVIEW_ANALYSIS_BATCH_SIZE=40
REVIEW_ANALYSIS_MAX_RETRIES=2
AI_AGENT_LLM_INTERVAL=300
AI_AGENT_RULE_INTERVAL=30
AI_AGENT_MIRROR_TTL_DAYS=30
AI_AGENT_BACKFILL_PAGE_SIZE=200
SOIL_MOISTURE_LOW=55.0
SOIL_MOISTURE_HIGH=70.0
FARM_NX=84
FARM_NY=106
ENV=production
LOG_LEVEL=INFO
DEFAULTS

# ────────────────────────────────────────────────
# 4b) shopping_mall 전용 .env.shop 생성 (신규)
#     - /farmos/prod/shop/* 키만 추출
#     - shop config.py 변수명 매핑 (lowercase → UPPER로, pydantic-settings 는 대소문자 무관)
# ────────────────────────────────────────────────
SHOP_ENV_FILE=/opt/farmos/.env.shop
jq -r '
    def shop_name_map(n):
      if   n == "/farmos/prod/shop/database_url"          then "DATABASE_URL"
      elif n == "/farmos/prod/shop/anniversary_api_key"   then "ANNIVERSARY_API_KEY"
      elif n == "/farmos/prod/shop/anthropic_api_key"     then "ANTHROPIC_API_KEY"
      elif n == "/farmos/prod/shop/claude_fallback_model" then "CLAUDE_FALLBACK_MODEL"
      elif n == "/farmos/prod/shop/embed_provider"        then "EMBED_PROVIDER"
      elif n == "/farmos/prod/shop/embed_model"           then "EMBED_MODEL"
      elif n == "/farmos/prod/shop/embed_api_key"         then "EMBED_API_KEY"
      elif n == "/farmos/prod/shop/embed_base_url"        then "EMBED_BASE_URL"
      elif n == "/farmos/prod/shop/reranker_model"        then "RERANKER_MODEL"
      elif n == "/farmos/prod/shop/rag_distance_threshold"        then "RAG_DISTANCE_THRESHOLD"
      elif n == "/farmos/prod/shop/rag_storage_distance_threshold" then "RAG_STORAGE_DISTANCE_THRESHOLD"
      elif n == "/farmos/prod/shop/rag_storage_retry_threshold"    then "RAG_STORAGE_RETRY_THRESHOLD"
      elif n == "/farmos/prod/shop/agent_max_iterations"  then "AGENT_MAX_ITERATIONS"
      elif n == "/farmos/prod/shop/use_multi_agent"       then "USE_MULTI_AGENT"
      elif n == "/farmos/prod/shop/langchain_tracing_v2"  then "LANGCHAIN_TRACING_V2"
      elif n == "/farmos/prod/shop/langchain_api_key"     then "LANGCHAIN_API_KEY"
      elif n == "/farmos/prod/shop/langchain_project"     then "LANGCHAIN_PROJECT"
      elif n == "/farmos/prod/shop/allow_origins"         then "ALLOW_ORIGINS"
      elif n == "/farmos/prod/shop/farmos_api_url"        then "FARMOS_API_URL"
      else
        n | sub("/farmos/prod/shop/"; "") | gsub("/"; "_") | ascii_upcase
      end;
    # @sh: ALLOW_ORIGINS 같은 JSON 배열 값에 포함된 따옴표가 그대로 .env.shop 으로 들어가지 않도록
    # single-quote 으로 감싸 shell/docker 양쪽에서 안전하게 파싱되게 함
    .Parameters[] | "\(shop_name_map(.Name))=\(.Value | @sh)"
  ' < "$TMP_SHOP_JSON" > "$SHOP_ENV_FILE"

# 빈 파일이면 placeholder 한 줄 추가 (compose env_file: 빈 파일 거부 방지)
if [ ! -s "$SHOP_ENV_FILE" ]; then
  echo "# shop SSM keys not yet seeded — using farmos shared .env only" > "$SHOP_ENV_FILE"
fi

# shop default 추가 (config.py default 와 일치, 운영 가시성)
cat >> "$SHOP_ENV_FILE" <<'SHOP_DEFAULTS'
POLICY_DOCS_DIR=/app/ai/docs
SHOP_DEFAULTS

chmod 600 "$SHOP_ENV_FILE"
chown ubuntu:ubuntu "$SHOP_ENV_FILE"
LOG "Generated $SHOP_ENV_FILE with $SHOP_PARAM_COUNT shop-specific keys"

# ────────────────────────────────────────────────
# 5) 권한 잠금
# ────────────────────────────────────────────────
chmod 600 "$ENV_FILE"
chown ubuntu:ubuntu "$ENV_FILE"

# ────────────────────────────────────────────────
# 6) 28키 + 6핵심 변수 사전 검증 (R13 완화)
# ────────────────────────────────────────────────
LINE_COUNT=$(wc -l < "$ENV_FILE")
LOG "SSM keys: $PARAM_COUNT (expected 28), .env lines: $LINE_COUNT"
if [ "$PARAM_COUNT" -lt 28 ]; then
  ERROR "Expected at least 28 SSM parameters, got $PARAM_COUNT"
  exit 1
fi

for KEY in DATABASE_URL JWT_SECRET_KEY LITELLM_URL LITELLM_API_KEY UPSTAGE_API_KEY POSTGRES_PASSWORD; do
  if ! grep -q "^${KEY}=" "$ENV_FILE"; then
    ERROR "Critical variable $KEY missing in .env (R13)"
    exit 1
  fi
done

# ────────────────────────────────────────────────
# 7) .env syntax check (R13 추가 안전장치)
#    잘못된 따옴표/이스케이프로 source 가 깨지면 컨테이너 부팅 시 변수 누락.
#    여기서 사전 검출.
# ────────────────────────────────────────────────
if ! ( set -a; source "$ENV_FILE"; set +a ) >/dev/null 2>&1; then
  ERROR ".env syntax check failed — bash 'source' returned non-zero"
  exit 1
fi

# .env.shop 도 동일하게 검증 (따옴표/이스케이프 문제 사전 검출)
if ! ( set -a; source "$SHOP_ENV_FILE"; set +a ) >/dev/null 2>&1; then
  ERROR ".env.shop syntax check failed — bash 'source' returned non-zero"
  exit 1
fi

LOG ".env / .env.shop generated and validated"
exit 0
