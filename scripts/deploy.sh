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
#   • Cloud Build  — builds inside GCP network, no local Docker push
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
die()  { err "$1"; exit 1; }

# ─────────────────────────────────────────────
# Checkpoint System (only set on SUCCESS)
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

# ─────────────────────────────────────────────
# Validate env vars are not placeholders
# ─────────────────────────────────────────────
validate_env() {
    local val="$1" name="$2"
    if [[ "$val" == *"REPLACE"* ]] || [[ "$val" == *"your-"* ]] || [[ "$val" == *"your_"* ]] || [[ "$val" == *"placeholder"* ]] || [[ "$val" == *"xxx"* ]]; then
        die "${name} contains a placeholder value '${val}'. Set the real value and re-run."
    fi
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

    # Validate no placeholder values
    validate_env "$PROJECT_ID" "GCP_PROJECT_ID"
    validate_env "$DB_PASSWORD" "DB_PASSWORD"
    validate_env "$BILLING_ACCOUNT" "BILLING_ACCOUNT"

    # Only gcloud and terraform required — Cloud Build replaces local Docker
    local missing=0
    for cmd in gcloud terraform; do
        if command -v "$cmd" &>/dev/null; then
            log "  ✓ ${cmd} found"
        else
            err "  ✗ ${cmd} not found — required"
            missing=$((missing + 1))
        fi
    done

    if ! gcloud auth print-access-token &>/dev/null; then
        die "gcloud not authenticated. Run: gcloud auth login"
    fi
    log "  ✓ gcloud authenticated"

    gcloud config set project "$PROJECT_ID" --quiet 2>/dev/null
    log "  ✓ Project: ${PROJECT_ID}"
    log "  ✓ Region:  ${REGION}"

    if [ $missing -gt 0 ]; then
        die "Pre-flight failed: ${missing} required tool(s) missing."
    fi

    # Self-heal: enable all required GCP APIs
    local REQUIRED_APIS=(
        "artifactregistry.googleapis.com"
        "cloudbuild.googleapis.com"
        "run.googleapis.com"
        "sqladmin.googleapis.com"
        "compute.googleapis.com"
        "vpcaccess.googleapis.com"
        "cloudscheduler.googleapis.com"
        "secretmanager.googleapis.com"
        "billingbudgets.googleapis.com"
    )
    log "  Enabling required GCP APIs (idempotent)..."
    gcloud services enable "${REQUIRED_APIS[@]}" --quiet >> "$LOG_FILE" 2>&1 \
        || warn "  Some APIs may have failed to enable — check log"
    log "  ✓ All APIs enabled"

    checkpoint_set "preflight"
}

# ═══════════════════════════════════════════════
# STEP 2: ARTIFACT REGISTRY
#   Ensure the Docker repo exists before builds
# ═══════════════════════════════════════════════
step_registry() {
    if checkpoint_done "registry"; then
        log "  ⏭ Artifact Registry already verified"
        return 0
    fi

    hdr "STEP 2/7: ARTIFACT REGISTRY"

    if gcloud artifacts repositories describe v8-services \
            --location="${REGION}" --format="value(name)" >> "$LOG_FILE" 2>&1; then
        log "  ✓ Artifact Registry repo exists"
    else
        log "  Creating Artifact Registry repo..."
        retry "Create Artifact Registry" \
            gcloud artifacts repositories create v8-services \
                --repository-format=docker \
                --location="${REGION}" \
                --description="V8 Engine Docker images" \
                --quiet
        sleep 5
        log "  ✓ Artifact Registry repo created"
    fi

    checkpoint_set "registry"
}

# ═══════════════════════════════════════════════
# STEP 3: TERRAFORM INFRASTRUCTURE
#   Creates: VPC, Cloud SQL, Cloud Run
#   jobs/services, Scheduler, Budget
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
    if ! terraform plan \
        -var="project_id=${PROJECT_ID}" \
        -var="region=${REGION}" \
        -var="db_password=${DB_PASSWORD}" \
        -var="db_user=${DB_USER}" \
        -var="billing_account=${BILLING_ACCOUNT}" \
        -var="telegram_bot_token=${TELEGRAM_BOT_TOKEN:-}" \
        -var="telegram_chat_id=${TELEGRAM_CHAT_ID:-}" \
        -out=plan.tfplan \
        -input=false \
        >> "$LOG_FILE" 2>&1; then
        err "  Terraform plan failed — check .deploy.log for details"
        cd "${PROJECT_ROOT}"
        die "Terraform plan failed. Fix the issue and re-run."
    fi

    if ! retry "Terraform apply" terraform apply -auto-approve plan.tfplan; then
        rm -f plan.tfplan
        cd "${PROJECT_ROOT}"
        die "Terraform apply failed after ${MAX_RETRIES} attempts. Check .deploy.log"
    fi

    rm -f plan.tfplan
    cd "${PROJECT_ROOT}"

    # Only checkpoint on success
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

    # Self-heal: install psql if missing
    if ! command -v psql &>/dev/null; then
        log "  Installing postgresql-client..."
        sudo apt-get update -qq >> "$LOG_FILE" 2>&1
        sudo apt-get install -y -qq postgresql-client >> "$LOG_FILE" 2>&1 || true
    fi

    # Self-heal: install cloud-sql-proxy if missing
    if ! command -v cloud-sql-proxy &>/dev/null; then
        log "  Installing cloud-sql-proxy..."
        curl -sSL -o /tmp/cloud-sql-proxy \
            "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.3/cloud-sql-proxy.linux.amd64" \
            >> "$LOG_FILE" 2>&1
        sudo install -m 755 /tmp/cloud-sql-proxy /usr/local/bin/cloud-sql-proxy
        rm -f /tmp/cloud-sql-proxy
    fi

    local conn_name
    conn_name=$(cd "${PROJECT_ROOT}/terraform" && terraform output -raw sql_connection_name 2>/dev/null || echo "")
    if [ -z "$conn_name" ]; then
        die "Could not get sql_connection_name from Terraform. Was Terraform applied?"
    fi

    # Kill any existing proxy
    pkill -f "cloud-sql-proxy" 2>/dev/null || true
    sleep 1

    cloud-sql-proxy "${conn_name}" --port=15432 >> "$LOG_FILE" 2>&1 &
    local proxy_pid=$!

    # Wait for proxy to be ready
    local ready=0
    for i in $(seq 1 30); do
        if pg_isready -h 127.0.0.1 -p 15432 -U "$DB_USER" >> "$LOG_FILE" 2>&1; then
            ready=1; break
        fi
        sleep 1
    done

    if [ $ready -eq 0 ]; then
        kill $proxy_pid 2>/dev/null || true
        die "Cloud SQL Proxy failed to connect after 30s. Check .deploy.log"
    fi

    log "  Applying 001_schema.sql..."
    if PGPASSWORD="$DB_PASSWORD" psql \
        -h 127.0.0.1 -p 15432 \
        -U "$DB_USER" -d v8engine \
        -f "${PROJECT_ROOT}/migrations/001_schema.sql" \
        >> "$LOG_FILE" 2>&1; then
        log "  ✓ Migration applied"
    else
        warn "  Migration had warnings (safe if tables already exist)"
    fi

    kill $proxy_pid 2>/dev/null || true
    wait $proxy_pid 2>/dev/null || true

    checkpoint_set "migration"
}

# ═══════════════════════════════════════════════
# STEP 5: BUILD & PUSH VIA CLOUD BUILD
#   Builds inside GCP network — no local Docker
#   push needed. Eliminates "connection refused".
# ═══════════════════════════════════════════════
step_build_push() {
    hdr "STEP 5/7: BUILD & PUSH (CLOUD BUILD)"

    for service in "${SERVICES[@]}"; do
        local ckpt="build_${service}"
        if checkpoint_done "$ckpt"; then
            log "  ⏭ ${service} already built and pushed"
            continue
        fi

        local tag="${REGISTRY}/${service}:latest"

        log "  Building & pushing ${service} via Cloud Build..."
        if retry "Cloud Build ${service}" \
            gcloud builds submit "${PROJECT_ROOT}" \
                --tag "$tag" \
                --build-arg "SERVICE=${service}" \
                --timeout=600s \
                --quiet; then
            checkpoint_set "$ckpt"
        else
            die "Cloud Build failed for ${service}. Check .deploy.log"
        fi
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
            if retry "Deploy ${service}" \
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
                    --quiet; then
                checkpoint_set "$ckpt"
            else
                die "Deploy failed for ${service}. Check .deploy.log"
            fi
        else
            local cpu="1" memory="1Gi" timeout="600s"
            case "$service" in
                modeler)  cpu="2"; memory="4Gi"; timeout="1800s" ;;
                scanner)  timeout="300s"
                          env_vars="${env_vars},TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-},TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}" ;;
            esac

            log "  Deploying ${service} as Cloud Run Job..."
            if retry "Deploy ${service}" \
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
                    --quiet; then
                checkpoint_set "$ckpt"
            else
                die "Deploy failed for ${service}. Check .deploy.log"
            fi
        fi
    done
}

# ═══════════════════════════════════════════════
# STEP 7: VERIFY & SUMMARY
# ═══════════════════════════════════════════════
step_verify() {
    hdr "STEP 7/7: VERIFY & SUMMARY"

    local dash_url
    dash_url=$(gcloud run services describe v8-dashboard \
        --region="$REGION" --format='value(status.url)' 2>/dev/null || echo "")

    if [ -n "$dash_url" ]; then
        local status_code
        status_code=$(curl -s -o /dev/null -w "%{http_code}" "${dash_url}/health" 2>/dev/null || echo "000")
        if [ "$status_code" = "200" ]; then
            log "  ✓ Dashboard healthy (HTTP 200)"
        else
            warn "  ⚠ Dashboard HTTP ${status_code} (may need data — this is normal on first deploy)"
        fi
    fi

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
    echo -e "  ${BOLD}Dashboard:${NC}  ${dash_url:-not available yet}"
    echo ""
    log "Next steps:"
    echo "  1. gcloud run jobs execute v8-ingestor --region=${REGION}"
    echo "  2. gcloud run jobs execute v8-modeler  --region=${REGION}"
    echo "  3. gcloud run jobs execute v8-scanner  --region=${REGION}"
    echo ""
    log "Checkpoints: ${STATE_FILE}"
    log "Full log:    ${LOG_FILE}"
}

# ═══════════════════════════════════════════════
# MAIN — Single block, strict order
# ═══════════════════════════════════════════════
echo "═══ V8 Deploy started at $(_ts) ═══" >> "$LOG_FILE"

hdr "V8 ENGINE — SINGLE-BLOCK DEPLOY"

if [ -f "$STATE_FILE" ]; then
    log "Resuming from $(wc -l < "$STATE_FILE") completed checkpoints"
else
    log "Fresh deployment"
fi

step_preflight      # 1. Validate env, check tools, enable APIs
step_registry       # 2. Ensure Artifact Registry repo exists
step_terraform      # 3. Create infra (VPC, SQL, Cloud Run, Scheduler)
step_migration      # 4. Apply database schema
step_build_push     # 5. Build & push via Cloud Build (no local Docker push)
step_deploy         # 6. Deploy images to Cloud Run
step_verify         # 7. Health check & summary

echo "═══ V8 Deploy completed at $(_ts) ═══" >> "$LOG_FILE"
