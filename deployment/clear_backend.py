#!/usr/bin/env python3
"""Cleanup utility script to wipe Cloud SQL database tables and GCS buckets for fresh testing."""

import sys
import logging
import asyncio
import asyncpg
from google.cloud import storage
from ediscovery_review_assistant.config import config

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger("clear_backend")

async def wipe_postgres():
    logger.info("Connecting to Cloud SQL PostgreSQL database...")
    conn = await asyncpg.connect(
        user=config.db_user,
        password=config.db_password,
        database=config.db_name,
        host=config.db_host,
        port=int(config.db_port)
    )
    try:
        logger.info("Truncating table document_chunks...")
        await conn.execute("TRUNCATE TABLE document_chunks;")
        logger.info("PostgreSQL document_chunks table wiped successfully!")
    except Exception as e:
        logger.error(f"Failed to wipe PostgreSQL table: {e}")
        raise
    finally:
        await conn.close()

def wipe_gcs():
    logger.info("Connecting to Google Cloud Storage...")
    client = storage.Client(project=config.project_id)
    bucket = client.bucket(config.gcs_bucket)
    
    logger.info(f"Listing all blobs in GCS bucket gs://{config.gcs_bucket}...")
    blobs = list(bucket.list_blobs())
    if not blobs:
        logger.info("No blobs found in GCS bucket.")
        return
        
    # Filter out blobs whose prefix matches agent_engine/ to preserve reasoning engine bundles
    target_blobs = [b for b in blobs if not b.name.startswith("agent_engine/")]
    if not target_blobs:
        logger.info("No target case documents found in GCS to delete.")
        return
        
    logger.info(f"Found {len(target_blobs)} blobs to delete (preserving agent_engine/ bundles).")
    for blob in target_blobs:
        logger.info(f"Deleting gs://{config.gcs_bucket}/{blob.name}...")
        blob.delete()
    logger.info("GCS bucket wiped successfully!")

if __name__ == "__main__":
    logger.info("=== STARTING FULL DATA WIPE FOR TESTING ===")
    try:
        asyncio.run(wipe_postgres())
    except Exception as e:
        logger.error(f"PostgreSQL wipe aborted: {e}")
    try:
        wipe_gcs()
    except Exception as e:
        logger.error(f"GCS wipe aborted: {e}")
    logger.info("=== DATA WIPE COMPLETE ===")
