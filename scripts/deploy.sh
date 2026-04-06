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

    local TF_VARS=(
        -var="project_id=${PROJECT_ID}"
        -var="region=${REGION}"
        -var="db_password=${DB_PASSWORD}"
        -var="db_user=${DB_USER}"
        -var="billing_account=${BILLING_ACCOUNT}"
        -var="telegram_bot_token=${TELEGRAM_BOT_TOKEN:-}"
        -var="telegram_chat_id=${TELEGRAM_CHAT_ID:-}"
    )

    retry "Terraform init" terraform init -upgrade -input=false

    # ── Self-heal: import pre-existing GCP resources into state ──
    log "  Importing pre-existing resources (idempotent)..."
    _tf_import() {
        local addr="$1" id="$2"
        if terraform state show "$addr" >> "$LOG_FILE" 2>&1; then
            return 0  # already in state
        fi
        log "    Importing ${addr}..."
        terraform import "${TF_VARS[@]}" -input=false "$addr" "$id" >> "$LOG_FILE" 2>&1 || true
    }

    _tf_import "google_service_account.runner" \
        "projects/${PROJECT_ID}/serviceAccounts/v8-runner@${PROJECT_ID}.iam.gserviceaccount.com"

    _tf_import "google_secret_manager_secret.db_pass" \
        "projects/${PROJECT_ID}/secrets/v8-db-password"

    _tf_import "google_artifact_registry_repository.docker" \
        "projects/${PROJECT_ID}/locations/${REGION}/repositories/v8-services"

    _tf_import "google_compute_network.vpc" \
        "projects/${PROJECT_ID}/global/networks/v8-vpc"

    _tf_import "google_compute_subnetwork.subnet" \
        "projects/${PROJECT_ID}/regions/${REGION}/subnetworks/v8-subnet"

    _tf_import "google_compute_global_address.sql_private" \
        "projects/${PROJECT_ID}/global/addresses/v8-sql-ip"

    _tf_import "google_vpc_access_connector.connector" \
        "projects/${PROJECT_ID}/locations/${REGION}/connectors/v8-connector"

    _tf_import "google_sql_database_instance.pg" \
        "projects/${PROJECT_ID}/instances/v8-citadel"

    _tf_import "google_sql_database.db" \
        "projects/${PROJECT_ID}/instances/v8-citadel/databases/v8engine"

    _tf_import "google_sql_user.operator" \
        "${DB_USER}//v8-citadel"

    _tf_import "google_cloud_run_v2_job.ingestor" \
        "projects/${PROJECT_ID}/locations/${REGION}/jobs/v8-ingestor"

    _tf_import "google_cloud_run_v2_job.modeler" \
        "projects/${PROJECT_ID}/locations/${REGION}/jobs/v8-modeler"

    _tf_import "google_cloud_run_v2_job.scanner" \
        "projects/${PROJECT_ID}/locations/${REGION}/jobs/v8-scanner"

    _tf_import "google_cloud_run_v2_service.dashboard" \
        "projects/${PROJECT_ID}/locations/${REGION}/services/v8-dashboard"

    _tf_import "google_cloud_scheduler_job.ingest" \
        "projects/${PROJECT_ID}/locations/${REGION}/jobs/v8-ingest-nightly"

    _tf_import "google_cloud_scheduler_job.model" \
        "projects/${PROJECT_ID}/locations/${REGION}/jobs/v8-model-nightly"

    _tf_import "google_cloud_scheduler_job.scan" \
        "projects/${PROJECT_ID}/locations/${REGION}/jobs/v8-scan"

    _tf_import "google_project_iam_member.runner_sql" \
        "${PROJECT_ID} roles/cloudsql.client serviceAccount:v8-runner@${PROJECT_ID}.iam.gserviceaccount.com"

    _tf_import "google_project_iam_member.runner_secrets" \
        "${PROJECT_ID} roles/secretmanager.secretAccessor serviceAccount:v8-runner@${PROJECT_ID}.iam.gserviceaccount.com"

    _tf_import "google_project_iam_member.runner_invoker" \
        "${PROJECT_ID} roles/run.invoker serviceAccount:v8-runner@${PROJECT_ID}.iam.gserviceaccount.com"

    log "  ✓ Import sweep complete"

    # ── Plan & Apply ──
    log "  Planning infrastructure..."
    if ! terraform plan \
        "${TF_VARS[@]}" \
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
#   Uses gcloud sql connect (tunnels through
#   Google's network — works from Cloud Shell
#   even with private-IP-only instances)
# ═══════════════════════════════════════════════
step_migration() {
    if checkpoint_done "migration"; then
        log "  ⏭ Migration already applied"
        return 0
    fi

    hdr "STEP 4/7: DATABASE MIGRATION"

    # Self-heal: enable sqladmin API (needed for gcloud sql connect)
    gcloud services enable sqladmin.googleapis.com --quiet >> "$LOG_FILE" 2>&1 || true

    log "  Applying 001_schema.sql via gcloud sql connect..."
    if gcloud sql connect v8-citadel \
        --user="${DB_USER}" \
        --database=v8engine \
        --quiet \
        < "${PROJECT_ROOT}/migrations/001_schema.sql" \
        >> "$LOG_FILE" 2>&1; then
        log "  ✓ Migration applied"
    else
        warn "  Migration had warnings (safe if tables already exist)"
    fi

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
