#!/bin/bash
# Exit immediately if a command exits with a non-zero status
set -e

# Load environment variables from .env file
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_ROOT/.env" ]; then
    echo "🔑 Loading environment from $PROJECT_ROOT/.env..."
    # Read variables from .env file, ignoring comments and blank lines
    export $(grep -v '^#' "$PROJECT_ROOT/.env" | grep -v '^\s*$' | xargs)
fi

# Dynamically resolve Project ID and Location
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
AGENT_GATEWAY="${AGENT_GATEWAY:-ediscovery-safety-policy}"

if [ -z "$PROJECT_ID" ]; then
    echo "❌ Error: GCP Project ID could not be resolved. Please set GOOGLE_CLOUD_PROJECT env var or configure active gcloud project." >&2
    exit 1
fi

echo "=================================================="
echo "🚀 Starting Backend Deployment to Vertex AI"
echo "=================================================="
echo "Project:  ${PROJECT_ID}"
echo "Location: ${LOCATION}"
echo "=================================================="

# 1. Build the local python wheel file
echo -e "\n📦 Step 1: Building python source distribution wheel package offline..."
uv build --offline

# 2. Execute deploy.py script to publish reasoning engine
echo -e "\n⚙️ Step 2: Creating and registering Reasoning Engine on Vertex AI..."
GATEWAY_FLAG=""
if [ -n "${AGENT_GATEWAY}" ]; then
    GATEWAY_FLAG="--agent_gateway ${AGENT_GATEWAY}"
    echo "Using Agent Gateway ingress binding: ${AGENT_GATEWAY}"
fi

uv run python deployment/deploy.py --create --project_id "${PROJECT_ID}" --location "${LOCATION}" ${GATEWAY_FLAG}

# 4. Extract new engine ID and propagate to files
if [ -f deployment/new_engine_id.txt ]; then
    NEW_ID=$(cat deployment/new_engine_id.txt)
    echo -e "\n🔑 Step 3: Propagating newly deployed Engine ID: ${NEW_ID}"
    
    # Update local .env
    sed -i "s/^VERTEX_AGENT_ENGINE_ID=.*/VERTEX_AGENT_ENGINE_ID=${NEW_ID}/" .env
    echo "- Updated local .env config"
    
    # Clean up temp file
    rm deployment/new_engine_id.txt
else
    echo -e "\n⚠️ Warning: deployment/new_engine_id.txt not found. Skip updating configs."
fi

echo -e "\n=================================================="
echo "🎉 Backend Deployment Successful!"
echo "Your reasoning engine has been updated in Vertex AI."
echo "=================================================="
