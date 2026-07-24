#!/bin/bash
# Exit immediately if a command exits with a non-zero status
set -e

# Configuration
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
REGION="${LOCATION}"

if [ -z "$PROJECT_ID" ]; then
    echo "❌ Error: GCP Project ID could not be resolved. Please set GOOGLE_CLOUD_PROJECT env var or configure active gcloud project." >&2
    exit 1
fi

IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/ediscovery-repo/ediscovery-frontend:latest"
SERVICE_NAME="ediscovery-review-assistant"

# Load environment variables from .env file
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_ROOT/.env" ]; then
    echo "🔑 Loading environment from $PROJECT_ROOT/.env..."
    # Read variables from .env file, ignoring comments and blank lines
    export $(grep -v '^#' "$PROJECT_ROOT/.env" | grep -v '^\s*$' | xargs)
fi

REASONING_ENGINE_ID="${VERTEX_AGENT_ENGINE_ID}"
GCS_BUCKET="${GOOGLE_CLOUD_STORAGE_BUCKET}"

if [ -z "$REASONING_ENGINE_ID" ]; then
    echo "❌ Error: VERTEX_AGENT_ENGINE_ID is not set in environment or .env file." >&2
    exit 1
fi

if [ -z "$GCS_BUCKET" ]; then
    echo "❌ Error: GOOGLE_CLOUD_STORAGE_BUCKET is not set in environment or .env file." >&2
    exit 1
fi

echo "=================================================="
echo "🚀 Starting Frontend Deployment to Google Cloud Run"
echo "=================================================="
echo "Project: ${PROJECT_ID}"
echo "Region:  ${REGION}"
echo "Image:   ${IMAGE_NAME}"
echo "Backend: Reasoning Engine ID (${REASONING_ENGINE_ID})"
echo "=================================================="

# 1. Submit build to Google Cloud Builds
echo -e "\n📦 Step 1: Building and pushing Docker image to Artifact Registry via Cloud Builds..."
gcloud builds submit --tag "${IMAGE_NAME}" --project "${PROJECT_ID}"

# 2. Deploy to Cloud Run
echo -e "\n🚢 Step 2: Deploying container to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
    --image "${IMAGE_NAME}" \
    --platform managed \
    --region "${REGION}" \
    --project "${PROJECT_ID}" \
    --add-cloudsql-instances="${PROJECT_ID}:${LOCATION}:ediscovery-db" \
    --set-env-vars "USE_CLOUD_AGENT=true,VERTEX_AGENT_ENGINE_ID=${REASONING_ENGINE_ID},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${LOCATION},GOOGLE_CLOUD_STORAGE_BUCKET=${GCS_BUCKET},GOOGLE_GENAI_USE_VERTEXAI=1,DB_USER=app_user,DB_PASSWORD=ediscovery_pass_123,DB_NAME=ediscovery,DB_HOST=/cloudsql/${PROJECT_ID}:${LOCATION}:ediscovery-db,DB_PORT=5432,MODEL_ARMOR_PROJECT_ID=${MODEL_ARMOR_PROJECT_ID},MODEL_ARMOR_LOCATION=${MODEL_ARMOR_LOCATION},MODEL_ARMOR_TEMPLATE_ID=${MODEL_ARMOR_TEMPLATE_ID}" \
    --memory=2Gi \
    --allow-unauthenticated

# 3. Print output URL
echo -e "\n=================================================="
echo "🎉 Deployment Successful!"
echo "You can access your E-Discovery Review Assistant dashboard online."
echo "=================================================="
