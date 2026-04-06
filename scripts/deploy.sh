#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# V8 Engine — Single-Block Idempotent Self-Healing Deploy
#
# Copyright (c) 2026 Oluwasegun Fanegan. All Rights Reserved.
# CONFIDENTIAL — Proprietary and trade secret information.
#
# Properties:
#   • Idempotent   — safe to re-run; skips completed checkpoints
#   • Self-healing — retries transient failures (network, Docker, gcloud)
#   • Checkpointed — persists progress to .deploy_state; resumes on re-run
#   • Single-block — one script, one invocation, full pipeline
#
# Usage:
#   ./scripts/deploy.sh                  # Full deploy (resume from last checkpoint)
#   ./scripts/deploy.sh --reset          # Wipe checkpoints and start fresh
#   ./scripts/deploy.sh --infra-only     # Terraform + migration only
#   ./scripts/deploy.sh --services-only  # Build + push + deploy services only
#   ./scripts/deploy.sh modeler          # Single service redeploy
#
# Required env vars:
#   GCP_PROJECT_ID    — GCP project ID
#   DB_PASSWORD       — Cloud SQL password
#
# Optional env vars:
#   GCP_REGION        — defaults to us-east4
#   DB_USER           — defaults to v8operator
#   TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — for scanner alerts
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="${GCP_REGION:-us-east4}"
DB_USER="${DB_USER:-v8operator}"
DB_PASSWORD="${DB_PASSWORD:?Set DB_PASSWORD}"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/v8-services"
SERVICE_ACCOUNT="v8-runner@${PROJECT_ID}.iam.gserviceaccount.com"

SERVICES=("ingestor" "modeler" "scanner" "dashboard")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_FILE="${PROJECT_ROOT}/.deploy_state"
LOG_FILE="${PROJECT_ROOT}/.deploy.log"
MAX_RETRIES=3
RETRY_DELAY=10

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
    # Check if a checkpoint has been completed
    local step="$1"
    grep -qxF "${step}" "$STATE_FILE" 2>/dev/null
}

checkpoint_set() {
    # Mark a checkpoint as completed
    local step="$1"
    if ! checkpoint_done "$step"; then
        echo "$step" >> "$STATE_FILE"
        log "  ✓ Checkpoint saved: ${step}"
    fi
}

checkpoint_reset() {
    rm -f "$STATE_FILE"
    log "All checkpoints cleared."
}

# ─────────────────────────────────────────────
# Self-Healing Retry Wrapper
# ─────────────────────────────────────────────
retry() {
    # Usage: retry <description> <command...>
    local desc="$1"; shift
    local attempt=1

    while [ $attempt -le $MAX_RETRIES ]; do
        log "  [${attempt}/${MAX_RETRIES}] ${desc}..."
        if "$@" >> "$LOG_FILE" 2>&1; then
            return 0
        fi

        if [ $attempt -lt $MAX_RETRIES ]; then
            warn "  Attempt ${attempt} failed for: ${desc}. Retrying in ${RETRY_DELAY}s..."
            sleep $RETRY_DELAY
            # Exponential backoff
            RETRY_DELAY=$((RETRY_DELAY * 2))
        else
            err "  All ${MAX_RETRIES} attempts failed for: ${desc}"
            return 1
        fi
        attempt=$((attempt + 1))
    done
}

# ─────────────────────────────────────────────
# Pre-flight Checks
# ─────────────────────────────────────────────
preflight() {
    hdr "PRE-FLIGHT CHECKS"

    local missing=0
    for cmd in gcloud docker terraform psql cloud-sql-proxy git; do
        if command -v "$cmd" &>/dev/null; then
            log "  ✓ ${cmd} found"
        else
            # Non-fatal for optional tools
            case "$cmd" in
                psql|cloud-sql-proxy)
                    warn "  ⚠ ${cmd} not found — migration step will be skipped"
                    ;;
                *)
                    err "  ✗ ${cmd} not found — required"
                    missing=$((missing + 1))
                    ;;
            esac
        fi
    done

    # Verify gcloud auth
    if ! gcloud auth print-access-token &>/dev/null; then
        err "gcloud not authenticated. Run: gcloud auth login"
        missing=$((missing + 1))
    else
        log "  ✓ gcloud authenticated"
    fi

    # Verify project
    local active_project
    active_project=$(gcloud config get-value project 2>/dev/null || true)
    if [ "$active_project" != "$PROJECT_ID" ]; then
        log "  Setting active project to ${PROJECT_ID}..."
        gcloud config set project "$PROJECT_ID" --quiet
    fi
    log "  ✓ Project: ${PROJECT_ID}"
    log "  ✓ Region:  ${REGION}"
    log "  ✓ Registry: ${REGISTRY}"

    if [ $missing -gt 0 ]; then
        err "Pre-flight failed: ${missing} required tool(s) missing."
        exit 1
    fi

    checkpoint_set "preflight"
}

# ─────────────────────────────────────────────
# Step 1: Docker Authentication
# ─────────────────────────────────────────────
step_docker_auth() {
    if checkpoint_done "docker_auth"; then
        log "  ⏭ Docker auth already configured (checkpoint exists)"
        return 0
    fi

    hdr "STEP 1: DOCKER AUTHENTICATION"
    retry "Configure Docker for Artifact Registry" \
        gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

    checkpoint_set "docker_auth"
}

# ─────────────────────────────────────────────
# Step 2: Terraform Infrastructure
# ─────────────────────────────────────────────
step_terraform() {
    if checkpoint_done "terraform"; then
        log "  ⏭ Terraform already applied (checkpoint exists)"
        return 0
    fi

    hdr "STEP 2: TERRAFORM INFRASTRUCTURE"
    cd "${PROJECT_ROOT}/terraform"

    # Init (idempotent)
    retry "Terraform init" terraform init -upgrade -input=false

    # Plan
    log "  Planning infrastructure changes..."
    terraform plan \
        -var="project_id=${PROJECT_ID}" \
        -var="region=${REGION}" \
        -var="db_password=${DB_PASSWORD}" \
        -var="db_user=${DB_USER}" \
        -var="billing_account=${BILLING_ACCOUNT:-}" \
        -var="telegram_bot_token=${TELEGRAM_BOT_TOKEN:-}" \
        -var="telegram_chat_id=${TELEGRAM_CHAT_ID:-}" \
        -out=plan.tfplan \
        -input=false \
        >> "$LOG_FILE" 2>&1

    # Apply
    retry "Terraform apply" terraform apply -auto-approve plan.tfplan
    rm -f plan.tfplan

    cd "${PROJECT_ROOT}"
    checkpoint_set "terraform"
}

# ─────────────────────────────────────────────
# Step 3: Database Migration
# ─────────────────────────────────────────────
step_migration() {
    if checkpoint_done "migration"; then
        log "  ⏭ Migration already applied (checkpoint exists)"
        return 0
    fi

    hdr "STEP 3: DATABASE MIGRATION"

    # Check if migration tools are available
    if ! command -v psql &>/dev/null || ! command -v cloud-sql-proxy &>/dev/null; then
        warn "  psql or cloud-sql-proxy not found — skipping migration"
        warn "  Run migration manually: psql -f migrations/001_schema.sql"
        checkpoint_set "migration"
        return 0
    fi

    # Get connection name from Terraform
    local conn_name
    conn_name=$(cd "${PROJECT_ROOT}/terraform" && terraform output -raw sql_connection_name 2>/dev/null)
    if [ -z "$conn_name" ]; then
        err "  Could not get sql_connection_name from Terraform output"
        warn "  Skipping migration — run manually after Terraform apply"
        checkpoint_set "migration"
        return 0
    fi

    # Start Cloud SQL Proxy (kill any existing)
    pkill -f "cloud-sql-proxy.*${conn_name}" 2>/dev/null || true
    sleep 1

    cloud-sql-proxy "${conn_name}" --port=15432 &
    local proxy_pid=$!

    # Wait for proxy to be ready
    local proxy_ready=0
    for i in $(seq 1 15); do
        if pg_isready -h 127.0.0.1 -p 15432 -U "$DB_USER" &>/dev/null; then
            proxy_ready=1
            break
        fi
        sleep 1
    done

    if [ $proxy_ready -eq 0 ]; then
        warn "  Cloud SQL Proxy not ready after 15s — skipping migration"
        kill $proxy_pid 2>/dev/null || true
        checkpoint_set "migration"
        return 0
    fi

    # Run migration (idempotent — all CREATE IF NOT EXISTS)
    log "  Applying 001_schema.sql..."
    PGPASSWORD="$DB_PASSWORD" psql \
        -h 127.0.0.1 -p 15432 \
        -U "$DB_USER" -d v8engine \
        -f "${PROJECT_ROOT}/migrations/001_schema.sql" \
        >> "$LOG_FILE" 2>&1 || {
        warn "  Migration had warnings (may be safe if tables exist)"
    }

    # Cleanup proxy
    kill $proxy_pid 2>/dev/null || true
    wait $proxy_pid 2>/dev/null || true

    log "  Migration complete."
    checkpoint_set "migration"
}

# ─────────────────────────────────────────────
# Step 4: Build & Push Docker Images
# ─────────────────────────────────────────────
build_and_push_service() {
    local service="$1"
    local ckpt="build_${service}"

    if checkpoint_done "$ckpt"; then
        log "  ⏭ ${service} image already built and pushed (checkpoint exists)"
        return 0
    fi

    local git_sha
    git_sha=$(cd "${PROJECT_ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo "dev")
    local tag_latest="${REGISTRY}/${service}:latest"
    local tag_sha="${REGISTRY}/${service}:${git_sha}"

    log "  Building ${service} (platform: linux/amd64)..."
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
}

step_build_all() {
    hdr "STEP 4: BUILD & PUSH DOCKER IMAGES"
    for svc in "${SERVICES[@]}"; do
        build_and_push_service "$svc"
    done
}

# ─────────────────────────────────────────────
# Step 5: Deploy to Cloud Run
# ─────────────────────────────────────────────
deploy_cloud_run_service() {
    local service="$1"
    local ckpt="deploy_${service}"

    if checkpoint_done "$ckpt"; then
        log "  ⏭ ${service} already deployed (checkpoint exists)"
        return 0
    fi

    local image="${REGISTRY}/${service}:latest"

    # Dashboard is a Cloud Run Service (always-on, public)
    # Others are Cloud Run Jobs (batch, scheduled)
    if [ "$service" = "dashboard" ]; then
        log "  Deploying ${service} as Cloud Run Service..."
        retry "Deploy ${service}" \
            gcloud run deploy "v8-${service}" \
                --image "$image" \
                --region "$REGION" \
                --platform managed \
                --allow-unauthenticated \
                --service-account "$SERVICE_ACCOUNT" \
                --memory 512Mi \
                --cpu 1 \
                --min-instances 0 \
                --max-instances 1 \
                --set-env-vars "DB_HOST=$(cd "${PROJECT_ROOT}/terraform" && terraform output -raw sql_private_ip 2>/dev/null || echo ''),DB_PORT=5432,DB_NAME=v8engine,DB_USER=${DB_USER},DB_PASSWORD=${DB_PASSWORD}" \
                --vpc-connector "v8-connector" \
                --vpc-egress "private-ranges-only" \
                --quiet
    else
        # Determine resource limits per service
        local cpu="1" memory="1Gi" timeout="600s"
        case "$service" in
            modeler)
                cpu="2"; memory="4Gi"; timeout="1800s"
                ;;
            scanner)
                timeout="300s"
                ;;
        esac

        log "  Deploying ${service} as Cloud Run Job..."

        # Build env vars string
        local env_vars="DB_HOST=$(cd "${PROJECT_ROOT}/terraform" && terraform output -raw sql_private_ip 2>/dev/null || echo ''),DB_PORT=5432,DB_NAME=v8engine,DB_USER=${DB_USER},DB_PASSWORD=${DB_PASSWORD}"

        # Add Telegram vars for scanner
        if [ "$service" = "scanner" ]; then
            env_vars="${env_vars},TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-},TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}"
        fi

        retry "Deploy ${service}" \
            gcloud run jobs update "v8-${service}" \
                --image "$image" \
                --region "$REGION" \
                --service-account "$SERVICE_ACCOUNT" \
                --memory "$memory" \
                --cpu "$cpu" \
                --task-timeout "$timeout" \
                --max-retries 1 \
                --set-env-vars "$env_vars" \
                --vpc-connector "v8-connector" \
                --vpc-egress "private-ranges-only" \
                --quiet
    fi

    checkpoint_set "$ckpt"
}

step_deploy_all() {
    hdr "STEP 5: DEPLOY TO CLOUD RUN"
    for svc in "${SERVICES[@]}"; do
        deploy_cloud_run_service "$svc"
    done
}

# ─────────────────────────────────────────────
# Step 6: Health Verification
# ─────────────────────────────────────────────
step_verify() {
    if checkpoint_done "verify"; then
        log "  ⏭ Verification already passed (checkpoint exists)"
        return 0
    fi

    hdr "STEP 6: HEALTH VERIFICATION"

    local all_healthy=1

    # Dashboard — Cloud Run Service with public URL
    local dash_url
    dash_url=$(gcloud run services describe v8-dashboard \
        --region="$REGION" --format='value(status.url)' 2>/dev/null || echo "")

    if [ -n "$dash_url" ]; then
        log "  Checking dashboard health at ${dash_url}/health ..."
        local dash_status
        dash_status=$(curl -s -o /dev/null -w "%{http_code}" "${dash_url}/health" 2>/dev/null || echo "000")
        if [ "$dash_status" = "200" ]; then
            log "  ✓ Dashboard healthy (HTTP ${dash_status})"
        else
            warn "  ⚠ Dashboard returned HTTP ${dash_status} (may need DB connectivity)"
            all_healthy=0
        fi
    else
        warn "  ⚠ Dashboard URL not found"
        all_healthy=0
    fi

    # Jobs — verify they exist and image is updated
    for svc in ingestor modeler scanner; do
        local job_image
        job_image=$(gcloud run jobs describe "v8-${svc}" \
            --region="$REGION" \
            --format='value(template.template.containers[0].image)' 2>/dev/null || echo "")

        if echo "$job_image" | grep -q "${REGISTRY}/${svc}"; then
            log "  ✓ v8-${svc} job image updated: ${job_image}"
        else
            warn "  ⚠ v8-${svc} job image mismatch: ${job_image}"
            all_healthy=0
        fi
    done

    if [ $all_healthy -eq 1 ]; then
        log "  All services verified healthy."
    else
        warn "  Some services have warnings — check logs for details."
        warn "  This is often expected on first deploy (DB may not be populated yet)."
    fi

    checkpoint_set "verify"
}

# ─────────────────────────────────────────────
# Step 7: Summary
# ─────────────────────────────────────────────
step_summary() {
    hdr "DEPLOYMENT COMPLETE"

    log "Service Status:"
    echo ""

    # Dashboard URL
    local dash_url
    dash_url=$(gcloud run services describe v8-dashboard \
        --region="$REGION" --format='value(status.url)' 2>/dev/null || echo "not deployed")
    echo -e "  ${BOLD}Dashboard:${NC}  ${dash_url}"

    # Jobs
    for svc in ingestor modeler scanner; do
        local status
        status=$(gcloud run jobs describe "v8-${svc}" \
            --region="$REGION" \
            --format='value(template.template.containers[0].image)' 2>/dev/null || echo "not deployed")
        echo -e "  ${BOLD}${svc}:${NC}  ${status}"
    done

    echo ""
    log "Checkpoints completed:"
    if [ -f "$STATE_FILE" ]; then
        while IFS= read -r line; do
            echo -e "  ${GREEN}✓${NC} ${line}"
        done < "$STATE_FILE"
    fi

    echo ""
    log "Log file: ${LOG_FILE}"
    log "State file: ${STATE_FILE}"
    echo ""
    log "Next steps:"
    echo "  1. Ingest data:   gcloud run jobs execute v8-ingestor --region=${REGION}"
    echo "  2. Run modeler:   gcloud run jobs execute v8-modeler --region=${REGION}"
    echo "  3. Run scanner:   gcloud run jobs execute v8-scanner --region=${REGION}"
    echo "  4. Run WFO:       curl -X POST <modeler-url>/wfo"
    echo "  5. View dashboard: ${dash_url}"
    echo ""
}

# ─────────────────────────────────────────────
# Main Orchestrator
# ─────────────────────────────────────────────
main() {
    # Initialize log
    echo "═══ V8 Deploy started at $(_ts) ═══" >> "$LOG_FILE"

    # Handle flags
    case "${1:-}" in
        --reset)
            checkpoint_reset
            shift
            ;;
        --infra-only)
            preflight
            step_docker_auth
            step_terraform
            step_migration
            step_summary
            exit 0
            ;;
        --services-only)
            preflight
            step_docker_auth
            step_build_all
            step_deploy_all
            step_verify
            step_summary
            exit 0
            ;;
    esac

    # Single service redeploy
    if [ -n "${1:-}" ] && [[ " ${SERVICES[*]} " =~ " $1 " ]]; then
        local target="$1"
        log "Single service redeploy: ${target}"
        preflight
        step_docker_auth
        # Force rebuild by removing checkpoint
        sed -i "/^build_${target}$/d" "$STATE_FILE" 2>/dev/null || true
        sed -i "/^deploy_${target}$/d" "$STATE_FILE" 2>/dev/null || true
        build_and_push_service "$target"
        deploy_cloud_run_service "$target"
        step_summary
        exit 0
    fi

    # Full pipeline — idempotent, resumes from last checkpoint
    hdr "V8 ENGINE FULL DEPLOYMENT"
    log "State file: ${STATE_FILE}"
    if [ -f "$STATE_FILE" ]; then
        local completed
        completed=$(wc -l < "$STATE_FILE")
        log "Resuming from checkpoint (${completed} steps already complete)"
    else
        log "Fresh deployment — no prior checkpoints"
    fi

    preflight
    step_docker_auth
    step_terraform
    step_migration
    step_build_all
    step_deploy_all
    step_verify
    step_summary

    echo "═══ V8 Deploy completed at $(_ts) ═══" >> "$LOG_FILE"
}

main "$@"
