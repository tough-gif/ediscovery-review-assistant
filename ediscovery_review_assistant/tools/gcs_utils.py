"""GCS utilities for document archiving and ingestion audit logging."""

import json
import logging
from datetime import datetime
from google.cloud import storage
from ediscovery_review_assistant.config import config

logger = logging.getLogger(__name__)

def get_storage_client():
    """Initializes GCS storage client using project configured in config."""
    return storage.Client(project=config.project_id)

def upload_to_gcs(content: bytes, filename: str, custodian: str) -> str:
    """Uploads raw file bytes to GCS case bucket as system of record.
    
    Returns:
        The GCS URI string of the uploaded file.
    """
    if not config.gcs_bucket:
        logger.warning("GCS bucket is not configured. Skipping raw file upload.")
        return ""
        
    client = get_storage_client()
    bucket = client.bucket(config.gcs_bucket)
    
    # Path schema: gs://bucket/custodian_name/file_name
    blob_name = f"{custodian}/{filename}"
    blob = bucket.blob(blob_name)
    
    logger.info(f"Uploading raw file {filename} to GCS at gs://{config.gcs_bucket}/{blob_name}...")
    blob.upload_from_string(content)
    
    gcs_uri = f"gs://{config.gcs_bucket}/{blob_name}"
    logger.info(f"Upload complete: {gcs_uri}")
    return gcs_uri

def write_ingestion_manifest(filename: str, custodian: str, file_hash: str, status: str, chunk_count: int):
    """Appends an ingestion event to the centralized JSON manifest log in GCS."""
    if not config.gcs_bucket:
        return
        
    client = get_storage_client()
    bucket = client.bucket(config.gcs_bucket)
    blob_name = "audit_logs/ingestion_manifest.json"
    blob = bucket.blob(blob_name)
    
    # 1. Download existing log or initialize empty list
    manifest = []
    if blob.exists():
        try:
            content = blob.download_as_text()
            manifest = json.loads(content)
        except Exception as e:
            logger.error(f"Failed to read existing ingestion manifest log from GCS: {e}")
            
    # 2. Append new audit entry
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "filename": filename,
        "custodian": custodian,
        "sha256_hash": file_hash,
        "status": status,
        "chunk_count": chunk_count
    }
    manifest.append(entry)
    
    # 3. Write back to GCS
    try:
        logger.info(f"Writing updated ingestion manifest to GCS audit logs...")
        blob.upload_from_string(json.dumps(manifest, indent=2))
        logger.info("centralized ingestion audit log updated successfully.")
    except Exception as e:
        logger.error(f"Failed to write updated manifest to GCS: {e}")

def write_chat_audit_log(session_id: str, query: str, response: str, files_cited: list):
    """Appends a reviewer query event to the centralized chat history log in GCS."""
    if not config.gcs_bucket:
        return
        
    client = get_storage_client()
    bucket = client.bucket(config.gcs_bucket)
    blob_name = "audit_logs/chat_history.json"
    blob = bucket.blob(blob_name)
    
    # 1. Download existing log or initialize empty list
    history = []
    if blob.exists():
        try:
            content = blob.download_as_text()
            history = json.loads(content)
        except Exception as e:
            logger.error(f"Failed to read existing chat history log from GCS: {e}")
            
    # 2. Append new audit entry
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "session_id": session_id,
        "reviewer_id": "attorney_user",
        "query": query,
        "response": response,
        "files_cited": files_cited
    }
    history.append(entry)
    
    # 3. Write back to GCS
    try:
        logger.info(f"Writing updated chat history to GCS audit logs...")
        blob.upload_from_string(json.dumps(history, indent=2))
        logger.info("centralized chat history log updated successfully.")
    except Exception as e:
        logger.error(f"Failed to write updated chat history to GCS: {e}")

def write_system_audit_log(action: str, details: str):
    """Appends a system action (like RESET) to the centralized system events log in GCS."""
    if not config.gcs_bucket:
        return
        
    client = get_storage_client()
    bucket = client.bucket(config.gcs_bucket)
    blob_name = "audit_logs/system_events.json"
    blob = bucket.blob(blob_name)
    
    # 1. Download existing log or initialize empty list
    events = []
    if blob.exists():
        try:
            content = blob.download_as_text()
            events = json.loads(content)
        except Exception as e:
            logger.error(f"Failed to read existing system events log from GCS: {e}")
            
    # 2. Append new audit entry
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
        "user_id": "attorney_user",
        "details": details
    }
    events.append(entry)
    
    # 3. Write back to GCS
    try:
        logger.info(f"Writing updated system events to GCS audit logs...")
        blob.upload_from_string(json.dumps(events, indent=2))
        logger.info("centralized system events log updated successfully.")
    except Exception as e:
        logger.error(f"Failed to write updated system events to GCS: {e}")
