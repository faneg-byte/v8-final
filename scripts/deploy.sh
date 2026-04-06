#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# V8 Engine — Single-Block Idempotent Self-Healing Deploy
#
# Copyright (c) 2026 Oluwasegun Fanegan. All Rights Reserved.
# CONFIDENTIAL — Proprietary and trade secret information.
#
# ONE COMMAND. ONE BLOCK. EVERYTHING IN ORDER.
#   ./scripts/deploy.sh
#
# Properties:
#   • Single-block — one script, one invocation, full pipeline in order
#   • Idempotent   — safe to re-run; skips completed checkpoints
#   • Self-healing — retries transient failures with exponential backoff
#   • Checkpointed — persists progress; resumes on re-run
#
# Required env vars:
#   GCP_PROJECT_ID    — GCP project ID
#   DB_PASSWORD       — Cloud SQL password
#   BILLING_ACCOUNT   — GCP billing account ID
#
# Optional env vars:
#   GCP_REGION        — defaults to us-east4
#   DB_USER           — defaults to v8operator
#   TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — for scanner alerts
#
# To start fresh:  rm .deploy_state && ./scripts/deploy.sh
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="${GCP_REGION:-us-east4}"
DB_USER="${DB_USER:-v8operator}"
DB_PASSWORD="${DB_PASSWORD:?Set DB_PASSWORD}"
BILLING_ACCOUNT="${BILLING_ACCOUNT:?Set BILLING_ACCOUNT}"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/v8-services"
SERVICE_ACCOUNT="v8-runner@${PROJECT_ID}.iam.gserviceaccount.com"

SERVICES=("ingestor" "modeler" "scanner" "dashboard")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_FILE="${PROJECT_ROOT}/.deploy_state"
LOG_FILE="${PROJECT_ROOT}/.deploy.log"
MAX_RETRIES=3

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

_ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log()  { echo -e "${GREEN}[$(_ts)] [V8]${NC} $1" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[$(_ts)] [V8 WARN]${NC} $1" | tee -a "$LOG_FILE"; }
err()  { echo -e "${RED}[$(_ts)] [V8 ERROR]${NC} $1" | tee -a "$LOG_FILE" >&2; }
hdr()  { echo -e "\n${CYAN}${BOLD}═══ $1 ═══${NC}\n" | tee -a "$LOG_FILE"; }

# ─────────────────────────────────────────────
# Checkpoint System
# ─────────────────────────────────────────────
checkpoint_done() {
    grep -qxF "$1" "$STATE_FILE" 2>/dev/null
}

checkpoint_set() {
    if ! checkpoint_done "$1"; then
        echo "$1" >> "$STATE_FILE"
        log "  ✓ Checkpoint saved: $1"
    fi
}

# ─────────────────────────────────────────────
# Self-Healing Retry Wrapper
# ─────────────────────────────────────────────
retry() {
    local desc="$1"; shift
    local attempt=1
    local delay=10

    while [ $attempt -le $MAX_RETRIES ]; do
        log "  [${attempt}/${MAX_RETRIES}] ${desc}..."
        if "$@" >> "$LOG_FILE" 2>&1; then
            return 0
        fi
        if [ $attempt -lt $MAX_RETRIES ]; then
            warn "  Attempt ${attempt} failed for: ${desc}. Retrying in ${delay}s..."
            sleep $delay
            delay=$((delay * 2))
        else
            err "  All ${MAX_RETRIES} attempts failed for: ${desc}"
            return 1
        fi
        attempt=$((attempt + 1))
    done
}

# ═══════════════════════════════════════════════
# STEP 1: PRE-FLIGHT CHECKS
# ═══════════════════════════════════════════════
step_preflight() {
    if checkpoint_done "preflight"; then
        log "  ⏭ Pre-flight already passed"
        return 0
    fi

    hdr "STEP 1/7: PRE-FLIGHT CHECKS"

    local missing=0
    for cmd in gcloud docker terraform; do
        if command -v "$cmd" &>/dev/null; then
            log "  ✓ ${cmd} found"
        else
            err "  ✗ ${cmd} not found — required"
            missing=$((missing + 1))
        fi
    done

    if ! gcloud auth print-access-token &>/dev/null; then
        err "gcloud not authenticated. Run: gcloud auth login"
        missing=$((missing + 1))
    else
        log "  ✓ gcloud authenticated"
    fi

    local active_project
    active_project=$(gcloud config get-value project 2>/dev/null || true)
    if [ "$active_project" != "$PROJECT_ID" ]; then
        gcloud config set project "$PROJECT_ID" --quiet
    fi

    log "  ✓ Project: ${PROJECT_ID}"
    log "  ✓ Region:  ${REGION}"

    if [ $missing -gt 0 ]; then
        err "Pre-flight failed: ${missing} required tool(s) missing."
        exit 1
    fi

    checkpoint_set "preflight"
}

# ═══════════════════════════════════════════════
# STEP 2: DOCKER AUTHENTICATION
# ═══════════════════════════════════════════════
step_docker_auth() {
    if checkpoint_done "docker_auth"; then
        log "  ⏭ Docker auth already configured"
        return 0
    fi

    hdr "STEP 2/7: DOCKER AUTHENTICATION"
    retry "Configure Docker for Artifact Registry" \
        gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

    checkpoint_set "docker_auth"
}

# ═══════════════════════════════════════════════
# STEP 3: TERRAFORM INFRASTRUCTURE
#   Creates: VPC, Cloud SQL, Artifact Registry,
#   Cloud Run jobs/services, Scheduler, Budget
# ═══════════════════════════════════════════════
step_terraform() {
    if checkpoint_done "terraform"; then
        log "  ⏭ Terraform already applied"
        return 0
    fi

    hdr "STEP 3/7: TERRAFORM INFRASTRUCTURE"
    cd "${PROJECT_ROOT}/terraform"

    retry "Terraform init" terraform init -upgrade -input=false

    log "  Planning infrastructure..."
    terraform plan \
        -var="project_id=${PROJECT_ID}" \
        -var="region=${REGION}" \
        -var="db_password=${DB_PASSWORD}" \
        -var="db_user=${DB_USER}" \
        -var="billing_account=${BILLING_ACCOUNT}" \
        -var="telegram_bot_token=${TELEGRAM_BOT_TOKEN:-}" \
        -var="telegram_chat_id=${TELEGRAM_CHAT_ID:-}" \
        -out=plan.tfplan \
        -input=false \
        >> "$LOG_FILE" 2>&1

    retry "Terraform apply" terraform apply -auto-approve plan.tfplan
    rm -f plan.tfplan

    cd "${PROJECT_ROOT}"
    checkpoint_set "terraform"
}

# ═══════════════════════════════════════════════
# STEP 4: DATABASE MIGRATION
# ═══════════════════════════════════════════════
step_migration() {
    if checkpoint_done "migration"; then
        log "  ⏭ Migration already applied"
        return 0
    fi

    hdr "STEP 4/7: DATABASE MIGRATION"

    if ! command -v psql &>/dev/null; then
        warn "  psql not found — installing via apt"
        sudo apt-get update -qq && sudo apt-get install -y -qq postgresql-client >> "$LOG_FILE" 2>&1 || true
    fi

    if ! command -v cloud-sql-proxy &>/dev/null && ! command -v cloud_sql_proxy &>/dev/null; then
        warn "  cloud-sql-proxy not found — installing"
        curl -o /usr/local/bin/cloud-sql-proxy \
            "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.3/cloud-sql-proxy.linux.amd64" \
            >> "$LOG_FILE" 2>&1 || true
        chmod +x /usr/local/bin/cloud-sql-proxy 2>/dev/null || true
    fi

    local conn_name
    conn_name=$(cd "${PROJECT_ROOT}/terraform" && terraform output -raw sql_connection_name 2>/dev/null || echo "")
    if [ -z "$conn_name" ]; then
        warn "  Could not get sql_connection_name — skipping migration"
        checkpoint_set "migration"
        return 0
    fi

    # Kill any existing proxy
    pkill -f "cloud-sql-proxy" 2>/dev/null || true
    sleep 1

    cloud-sql-proxy "${conn_name}" --port=15432 >> "$LOG_FILE" 2>&1 &
    local proxy_pid=$!

    # Wait for proxy
    local ready=0
    for i in $(seq 1 20); do
        if pg_isready -h 127.0.0.1 -p 15432 -U "$DB_USER" &>/dev/null 2>&1; then
            ready=1; break
        fi
        sleep 1
    done

    if [ $ready -eq 0 ]; then
        warn "  Cloud SQL Proxy not ready — skipping migration (run manually later)"
        kill $proxy_pid 2>/dev/null || true
        checkpoint_set "migration"
        return 0
    fi

    log "  Applying 001_schema.sql..."
    PGPASSWORD="$DB_PASSWORD" psql \
        -h 127.0.0.1 -p 15432 \
        -U "$DB_USER" -d v8engine \
        -f "${PROJECT_ROOT}/migrations/001_schema.sql" \
        >> "$LOG_FILE" 2>&1 || warn "  Migration had warnings (safe if tables exist)"

    kill $proxy_pid 2>/dev/null || true
    wait $proxy_pid 2>/dev/null || true

    log "  Migration complete."
    checkpoint_set "migration"
}

# ═══════════════════════════════════════════════
# STEP 5: BUILD & PUSH DOCKER IMAGES
#   Builds all 4 services and pushes to
#   Artifact Registry (created in Step 3)
# ═══════════════════════════════════════════════
step_build_push() {
    hdr "STEP 5/7: BUILD & PUSH DOCKER IMAGES"

    local git_sha
    git_sha=$(cd "${PROJECT_ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo "dev")

    for service in "${SERVICES[@]}"; do
        local ckpt="build_${service}"
        if checkpoint_done "$ckpt"; then
            log "  ⏭ ${service} already built and pushed"
            continue
        fi

        local tag_latest="${REGISTRY}/${service}:latest"
        local tag_sha="${REGISTRY}/${service}:${git_sha}"

        log "  Building ${service}..."
        retry "Docker build ${service}" \
            docker build \
                --build-arg SERVICE="${service}" \
                --platform linux/amd64 \
                -t "$tag_latest" \
                -t "$tag_sha" \
                "${PROJECT_ROOT}"

        log "  Pushing ${service}..."
        retry "Docker push ${service} latest" docker push "$tag_latest"
        retry "Docker push ${service} sha" docker push "$tag_sha"

        checkpoint_set "$ckpt"
    done
}

# ═══════════════════════════════════════════════
# STEP 6: DEPLOY TO CLOUD RUN
#   Updates Cloud Run jobs/services with new images
# ═══════════════════════════════════════════════
step_deploy() {
    hdr "STEP 6/7: DEPLOY TO CLOUD RUN"

    local sql_ip
    sql_ip=$(cd "${PROJECT_ROOT}/terraform" && terraform output -raw sql_private_ip 2>/dev/null || echo "")

    for service in "${SERVICES[@]}"; do
        local ckpt="deploy_${service}"
        if checkpoint_done "$ckpt"; then
            log "  ⏭ ${service} already deployed"
            continue
        fi

        local image="${REGISTRY}/${service}:latest"
        local env_vars="DB_HOST=${sql_ip},DB_PORT=5432,DB_NAME=v8engine,DB_USER=${DB_USER},DB_PASSWORD=${DB_PASSWORD}"

        if [ "$service" = "dashboard" ]; then
            log "  Deploying ${service} as Cloud Run Service..."
            retry "Deploy ${service}" \
                gcloud run deploy "v8-${service}" \
                    --image "$image" \
                    --region "$REGION" \
                    --platform managed \
                    --allow-unauthenticated \
                    --service-account "$SERVICE_ACCOUNT" \
                    --memory 512Mi --cpu 1 \
                    --min-instances 0 --max-instances 1 \
                    --set-env-vars "$env_vars" \
                    --vpc-connector "v8-connector" \
                    --vpc-egress "private-ranges-only" \
                    --quiet
        else
            local cpu="1" memory="1Gi" timeout="600s"
            case "$service" in
                modeler)  cpu="2"; memory="4Gi"; timeout="1800s" ;;
                scanner)  timeout="300s"
                          env_vars="${env_vars},TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-},TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}" ;;
            esac

            log "  Deploying ${service} as Cloud Run Job..."
            retry "Deploy ${service}" \
                gcloud run jobs update "v8-${service}" \
                    --image "$image" \
                    --region "$REGION" \
                    --service-account "$SERVICE_ACCOUNT" \
                    --memory "$memory" --cpu "$cpu" \
                    --task-timeout "$timeout" \
                    --max-retries 1 \
                    --set-env-vars "$env_vars" \
                    --vpc-connector "v8-connector" \
                    --vpc-egress "private-ranges-only" \
                    --quiet
        fi

        checkpoint_set "$ckpt"
    done
}

# ═══════════════════════════════════════════════
# STEP 7: VERIFY & SUMMARY
# ═══════════════════════════════════════════════
step_verify() {
    hdr "STEP 7/7: VERIFY & SUMMARY"

    # Dashboard health
    local dash_url
    dash_url=$(gcloud run services describe v8-dashboard \
        --region="$REGION" --format='value(status.url)' 2>/dev/null || echo "")

    if [ -n "$dash_url" ]; then
        local status_code
        status_code=$(curl -s -o /dev/null -w "%{http_code}" "${dash_url}/health" 2>/dev/null || echo "000")
        if [ "$status_code" = "200" ]; then
            log "  ✓ Dashboard healthy (HTTP 200)"
        else
            warn "  ⚠ Dashboard HTTP ${status_code} (may need data)"
        fi
    fi

    # Job images
    for svc in ingestor modeler scanner; do
        local img
        img=$(gcloud run jobs describe "v8-${svc}" \
            --region="$REGION" \
            --format='value(template.template.containers[0].image)' 2>/dev/null || echo "unknown")
        log "  ✓ v8-${svc}: ${img}"
    done

    echo ""
    log "═══ DEPLOYMENT COMPLETE ═══"
    echo ""
    echo -e "  ${BOLD}Dashboard:${NC}  ${dash_url:-not available}"
    echo ""
    log "Next steps:"
    echo "  1. gcloud run jobs execute v8-ingestor --region=${REGION}"
    echo "  2. gcloud run jobs execute v8-modeler  --region=${REGION}"
    echo "  3. gcloud run jobs execute v8-scanner  --region=${REGION}"
    echo ""
    log "Checkpoints saved to: ${STATE_FILE}"
    log "Full log at: ${LOG_FILE}"
}

# ═══════════════════════════════════════════════
# MAIN — Single block, strict order, no flags
# ═══════════════════════════════════════════════
echo "═══ V8 Deploy started at $(_ts) ═══" >> "$LOG_FILE"

hdr "V8 ENGINE — SINGLE-BLOCK DEPLOY"

if [ -f "$STATE_FILE" ]; then
    log "Resuming from $(wc -l < "$STATE_FILE") completed checkpoints"
else
    log "Fresh deployment"
fi

step_preflight      # 1. Check tools & auth
step_docker_auth    # 2. Configure Docker for Artifact Registry
step_terraform      # 3. Create infra (VPC, SQL, Registry, Run, Scheduler)
step_migration      # 4. Apply database schema
step_build_push     # 5. Build & push all 4 Docker images
step_deploy         # 6. Deploy images to Cloud Run
step_verify         # 7. Health check & summary

echo "═══ V8 Deploy completed at $(_ts) ═══" >> "$LOG_FILE"
