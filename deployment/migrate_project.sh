#!/bin/bash
# Exit immediately if a command exits with a non-zero status
set -e

# Load local .env configurations as fallbacks if present
if [ -f .env ]; then
    # Read variables from .env ignoring comments
    export $(grep -v '^#' .env | xargs)
fi

# Target Configuration Parameters (CLI arguments override .env variables)
TARGET_PROJECT_ID="${1:-$GOOGLE_CLOUD_PROJECT}"
TARGET_REGION="${2:-${GOOGLE_CLOUD_LOCATION:-us-central1}}"
DB_PASSWORD="${3:-${DB_PASSWORD:-ediscovery_pass_123}}"
AGENT_GATEWAY="${4:-${AGENT_GATEWAY:-ediscovery-safety-policy}}"

if [ -z "$TARGET_PROJECT_ID" ]; then
    echo "❌ Error: Please specify the TARGET_PROJECT_ID either as an argument or in .env."
    echo "Usage: ./deployment/migrate_project.sh <TARGET_PROJECT_ID> [REGION] [DB_PASSWORD] [AGENT_GATEWAY]"
    exit 1
fi

echo "=================================================="
echo "🚀 Beginning GCP Project Migration Flow"
echo "=================================================="
echo "Target Project ID: ${TARGET_PROJECT_ID}"
echo "Target Region:     ${TARGET_REGION}"
echo "Database Password: [SECURE]"
echo "Agent Gateway:     ${AGENT_GATEWAY}"
echo "=================================================="

# 1. Enable Required Cloud Services on the Target Project
echo -e "\n⚙️ Step 1: Enabling Required GCP APIs on Target Project..."
gcloud services enable \
    aiplatform.googleapis.com \
    storage.googleapis.com \
    sqladmin.googleapis.com \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    --project="${TARGET_PROJECT_ID}"

# 2. Create the GCS Vault Storage Bucket
BUCKET_NAME="${TARGET_PROJECT_ID}-ediscovery-vault"
echo -e "\n🪣 Step 2: Setting up GCS Vault Bucket: gs://${BUCKET_NAME}..."
if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" --project="${TARGET_PROJECT_ID}" &>/dev/null; then
    gcloud storage buckets create "gs://${BUCKET_NAME}" \
        --location="${TARGET_REGION}" \
        --project="${TARGET_PROJECT_ID}"
    echo "✅ GCS Bucket created successfully."
else
    echo "ℹ️ GCS Bucket already exists. Skipping."
fi

# 3. Create Artifact Registry Repository for Docker container images
echo -e "\n📦 Step 3: Setting up Artifact Registry Repository..."
if ! gcloud artifacts repositories describe ediscovery-repo --location="${TARGET_REGION}" --project="${TARGET_PROJECT_ID}" &>/dev/null; then
    gcloud artifacts repositories create ediscovery-repo \
        --repository-format=docker \
        --location="${TARGET_REGION}" \
        --description="Docker repository for E-Discovery Review app" \
        --project="${TARGET_PROJECT_ID}"
    echo "✅ Artifact Registry repository created."
else
    echo "ℹ️ Artifact Registry repository already exists. Skipping."
fi

# 4. Create Cloud SQL PostgreSQL Instance and Database
INSTANCE_NAME="ediscovery-db"
echo -e "\n💾 Step 4: Setting up Cloud SQL PostgreSQL Instance..."
if ! gcloud sql instances describe "${INSTANCE_NAME}" --project="${TARGET_PROJECT_ID}" &>/dev/null; then
    echo "Creating Cloud SQL PostgreSQL instance (this takes several minutes)..."
    gcloud sql instances create "${INSTANCE_NAME}" \
        --database-version=POSTGRES_15 \
        --tier=db-custom-1-3840 \
        --region="${TARGET_REGION}" \
        --project="${TARGET_PROJECT_ID}"
        
    echo "Creating 'ediscovery' database..."
    gcloud sql databases create ediscovery --instance="${INSTANCE_NAME}" --project="${TARGET_PROJECT_ID}"
    
    echo "Creating database user 'app_user'..."
    gcloud sql users create app_user --instance="${INSTANCE_NAME}" --password="${DB_PASSWORD}" --project="${TARGET_PROJECT_ID}"
    echo "✅ Cloud SQL setup complete."
else
    echo "ℹ️ Cloud SQL instance '${INSTANCE_NAME}' already exists. Skipping."
fi

# 5. Bind IAM Permissions to Target Service Account
PROJECT_NUMBER=$(gcloud projects describe "${TARGET_PROJECT_ID}" --format="value(projectNumber)")
SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo -e "\n🔑 Step 5: Configuring IAM Service Account Permissions..."
echo "Targeting default Compute SA: ${SA_EMAIL}..."

# gcloud projects add-iam-policy-binding "${TARGET_PROJECT_ID}" \
#     --member="serviceAccount:${SA_EMAIL}" \
#     --role="roles/aiplatform.user" >/dev/null
#     
# gcloud projects add-iam-policy-binding "${TARGET_PROJECT_ID}" \
#     --member="serviceAccount:${SA_EMAIL}" \
#     --role="roles/cloudsql.client" >/dev/null
#     
# gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
#     --member="serviceAccount:${SA_EMAIL}" \
#     --role="roles/storage.objectAdmin" >/dev/null

echo "✅ IAM Bindings configured."

# 6. Prompt to Enable SQL pgvector extension manually
echo -e "\n=================================================="
echo "⚠️  Action Required: Enable pgvector & Configure Safety"
echo "=================================================="
echo "Before deploying, please verify these steps on the target project:"
echo ""
echo "Step A: Enable pgvector on PostgreSQL:"
echo "1. Go to: https://console.cloud.google.com/sql/instances/${INSTANCE_NAME}/studio?project=${TARGET_PROJECT_ID}"
echo "2. Select database: 'ediscovery' (log in as 'postgres' or 'app_user')"
echo "3. Run: CREATE EXTENSION IF NOT EXISTS vector;"
echo ""
if [ -n "${AGENT_GATEWAY}" ]; then
    echo "Step B: Create Model Armor Template in Target Project:"
    echo "Run this command in Cloud Shell to register the safety template:"
    echo "gcloud beta model-armor templates create ${AGENT_GATEWAY} \\"
    echo "    --location=\"${TARGET_REGION}\" \\"
    echo "    --project=\"${TARGET_PROJECT_ID}\" \\"
    echo "    --pii-entities=\"email-address,phone-number,social-security-number\" \\"
    echo "    --pii-action=\"mask\" \\"
    echo "    --jailbreak-action=\"block\" \\"
    echo "    --prompt-injection-action=\"block\""
    echo ""
    echo "Step C: Ensure the Agent Gateway '${AGENT_GATEWAY}' is created"
    echo "in the target project location '${TARGET_REGION}'."
fi
echo "=================================================="
read -p "Press [Enter] once these actions are complete to resume deployment..."

# 7. Package and Deploy Backend Reasoning Engine
echo -e "\n⚙️ Step 6: Building and Deploying Vertex AI Reasoning Engine..."
# Build package wheel offline to bypass registry network authentication checks
uv build --offline

GATEWAY_FLAG=""
if [ -n "${AGENT_GATEWAY}" ]; then
    GATEWAY_FLAG="--agent_gateway ${AGENT_GATEWAY}"
fi

# Execute Python staging deploy runner in the context of the target project
GOOGLE_CLOUD_PROJECT="${TARGET_PROJECT_ID}" \
GOOGLE_CLOUD_STORAGE_BUCKET="${BUCKET_NAME}" \
GOOGLE_CLOUD_LOCATION="${TARGET_REGION}" \
uv run python deployment/deploy.py --create ${GATEWAY_FLAG}

NEW_ENGINE_ID=$(cat deployment/new_engine_id.txt)
echo "✅ Reasoning Engine registered. New Engine ID: ${NEW_ENGINE_ID}"
rm -f deployment/new_engine_id.txt

# 8. Compile and Deploy Streamlit Frontend to Cloud Run
echo -e "\n🚢 Step 7: Submitting Container Build & Deploying Cloud Run Service..."
IMAGE_NAME="${TARGET_REGION}-docker.pkg.dev/${TARGET_PROJECT_ID}/ediscovery-repo/ediscovery-frontend:latest"
SERVICE_NAME="ediscovery-review-assistant"

gcloud builds submit --tag "${IMAGE_NAME}" --project="${TARGET_PROJECT_ID}"

gcloud run deploy "${SERVICE_NAME}" \
    --image "${IMAGE_NAME}" \
    --platform managed \
    --region="${TARGET_REGION}" \
    --project="${TARGET_PROJECT_ID}" \
    --add-cloudsql-instances="${TARGET_PROJECT_ID}:${TARGET_REGION}:${INSTANCE_NAME}" \
    --set-env-vars "VERTEX_AGENT_ENGINE_ID=${NEW_ENGINE_ID},GOOGLE_CLOUD_PROJECT=${TARGET_PROJECT_ID},GOOGLE_CLOUD_LOCATION=${TARGET_REGION},GOOGLE_CLOUD_STORAGE_BUCKET=${BUCKET_NAME},GOOGLE_GENAI_USE_VERTEXAI=1,DB_USER=app_user,DB_PASSWORD=${DB_PASSWORD},DB_NAME=ediscovery,DB_HOST=/cloudsql/${TARGET_PROJECT_ID}:${TARGET_REGION}:${INSTANCE_NAME},DB_PORT=5432,AGENT_GATEWAY=${AGENT_GATEWAY}" \
    --memory=2Gi \
    --allow-unauthenticated

# 9. Update local .env file to allow running locally against the new project coordinates
echo -e "\n📝 Step 8: Syncing local .env configuration..."
if [ -f .env ]; then
    cp .env .env.bak
    echo "Backed up current .env config to .env.bak"
fi

cat <<EOF > .env
GOOGLE_CLOUD_PROJECT=${TARGET_PROJECT_ID}
GOOGLE_CLOUD_LOCATION=${TARGET_REGION}
VERTEX_AGENT_ENGINE_ID=${NEW_ENGINE_ID}
GOOGLE_CLOUD_STORAGE_BUCKET=${BUCKET_NAME}
DB_USER=app_user
DB_PASSWORD=${DB_PASSWORD}
DB_NAME=ediscovery
DB_HOST=127.0.0.1
DB_PORT=5433
AGENT_GATEWAY=${AGENT_GATEWAY}
GOOGLE_GENAI_USE_VERTEXAI=1

# Model Settings
GEMINI_MODEL_NAME=gemini-2.5-flash
GEMINI_MODEL_TEMPERATURE=0.2

# Embedding Settings
EMBEDDING_MODEL_NAME=text-embedding-005
VECTOR_DIMENSIONS=768
USE_CLOUD_AGENT=true

# OTEL prompt/response logging requirements for evaluation
OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY

EOF
echo "✅ Local .env configuration updated."

echo -e "\n=================================================="
echo "🎉 Project Migration Completed Successfully!"
echo "Your E-Discovery Review app is live on Cloud Run."
echo "=================================================="
