# E-Discovery Document Review Assistant

The **E-Discovery Document Review Assistant** is an automated compliance and document interrogation dashboard built with the **Google ADK (Agent Development Kit)**. It enables legal counsel and compliance officers to upload discovery files (.eml, .txt, .pdf), index them securely, perform hybrid semantic/keyword search, detect attorney-client privilege in real time, and audit reviewer actions through a centralized Compliance Ledger.

---

## Architecture

The system utilizes a hybrid, multi-tenant architecture designed to manage security and forensic trails:

*   **Hybrid Memory Bank Service**: Coordinates memory writes and search queries across two backends:
    1.  **Vertex AI Cloud Memory Store**: Handles vector embeddings and semantic search queries.
    2.  **Cloud SQL PostgreSQL**: Handles keyword matching and workspace-isolated metadata queries.
*   **Document Ingestion Engine**: Parses files (.eml, .txt, .pdf), extracts custodian metadata, splits files into semantic chunks, and calculates SHA-256 hashes for de-duplication.
*   **Privilege Filter**: Scans model responses and source documents for legal counsel involvement or litigation strategy to flag potential attorney-client privilege or work product.
*   **Audit Logger**: Automatically writes central ingestion logs and chronological chat history records to GCS for compliance reviews.

---

## Prerequisites

*   **Python 3.13+**
*   **Google Cloud Project** with Vertex AI, Cloud Storage, and Cloud SQL Admin APIs enabled.
*   **[uv](https://github.com/astral-sh/uv)** for fast dependency and package management.
*   **Cloud SQL PostgreSQL Instance** with the `pgvector` extension configured.
*   A **Google Cloud Storage Bucket** (e.g., `ai-ml-learning-xwf-ediscovery-vault`) to host package staging, uploaded files, and compliance audit logs.

---

## Local Setup

All commands below must be executed from the root of the project directory.

### 1. Install Dependencies

Using `uv`, install the project dependencies:

```bash
uv sync
```

### 2. Start Cloud SQL Auth Proxy

To securely connect to the Cloud SQL database instance locally, start the auth proxy on port 5433:

```bash
./cloud-sql-proxy --port 5433 your-gcp-project-id:us-central1:ediscovery-db
```

### 3. Configure Environment

Create a `.env` file in the root directory based on the following template:

```env
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
VERTEX_AGENT_ENGINE_ID=your-vertex-reasoning-engine-id
GOOGLE_CLOUD_STORAGE_BUCKET=your-gcs-vault-bucket-name
DB_USER=app_user
DB_PASSWORD=ediscovery_pass_123
DB_NAME=ediscovery
DB_HOST=127.0.0.1
DB_PORT=5433
```

---

## Testing & Local Execution

Authenticate your application default credentials and launch the interactive Streamlit dashboard:

```bash
# Authenticate application default credentials
gcloud auth application-default login

# Launch the Streamlit application
uv run streamlit run streamlit_app.py --server.port 8501
```

Open `http://localhost:8501` in your browser.

---

## Deployment to Vertex AI (Backend)

The backend agent package must be built as a wheel file and registered as a Vertex AI Reasoning Engine.

### 1. Build the Agent Package

Package the python source codebase:

```bash
uv build --wheel --out-dir deployment
```

### 2. Register on Vertex AI

Execute the backend deployment script to push the packaged agent bundles to GCS and register it:

```bash
bash ./deployment/deploy_backend.sh
```

This will write the new reasoning engine ID to `deployment/new_engine_id.txt` and propagate it to your local config.

---

## Deploying the Frontend to Cloud Run

The frontend client can be deployed to **Google Cloud Run** for shared reviewer access.

### 1. Create Artifact Registry Repository

Only needed once per project:

```bash
gcloud artifacts repositories create ediscovery-repo \
    --repository-format=docker \
    --location=us-central1 \
    --description="Docker repository for E-Discovery Review app" \
    --project=your-gcp-project-id
```

### 2. Build and Deploy Container

Run the frontend deployment helper script:

```bash
bash ./deployment/deploy_frontend.sh
```

---

## Project Migration (Transferring to a new GCP Project)

To automate the migration of all backend resources, IAM permissions, database configurations, and Cloud Run frontends to a completely new GCP project, use our unified migration script `deployment/migrate_project.sh`.

### Option A: Configure `.env` first (Recommended)
1.  Open your local `.env` configuration file and update the variables to point to the new project coordinates:
    ```env
    GOOGLE_CLOUD_PROJECT=your-target-project-id
    GOOGLE_CLOUD_LOCATION=us-central1
    GOOGLE_CLOUD_STORAGE_BUCKET=your-target-gcs-bucket
    DB_PASSWORD=your-target-db-password
    ```
2.  Execute the migration script without arguments:
    ```bash
    ./deployment/migrate_project.sh
    ```

### Option B: Pass arguments via CLI
Alternatively, run the script and specify parameters directly:
```bash
./deployment/migrate_project.sh <TARGET_PROJECT_ID> [TARGET_REGION] [DB_PASSWORD]
```

*Note: During execution, the script will pause and output a direct Cloud Console Query Studio URL. You must open that link, log in, and run `CREATE EXTENSION IF NOT EXISTS vector;` to enable pgvector before pressing [Enter] to finish the deployment.*

---

## Usage Guide

The dashboard is structured into two main workspaces:

1.  **💬 Case Investigation**:
    *   **Document Ingestion (Sidebar)**: Drag and drop files, assign a Custodian, and click `🚀 Process & Ingest`. The app automatically calculates file hashes and skips duplicates.
    *   **Chat Console**: Ask questions about the case files. A circular loader spinner is displayed during evidence retrieval, and answers will contain clickable `[SOURCE: file_name]` tags.
    *   **Privilege Alerts**: If the system detects privileged material in the retrieved evidence, it will display a red alert: `⚠️ Attorney-Client Privileged / Work Product`.

2.  **📋 Compliance & Audit Trail**:
    *   **Compliance Summary**: High-level counters tracking **Total Files Audited** and **Active Custodians**.
    *   **Chain of Custody Ingestion Ledger**: A tabular ledger tracking the timestamp, name, custodian, status, and chunk count of all uploads. (Cryptographic hashes are hidden in the UI but preserved in reports).
    *   **Counsel Chat Audit Trail**: Chronologically sorted list of chat sessions named by date and first query. Reviewers can select a session and expand the `🔍 View Complete Chat Transcript` expander to check transcripts.
