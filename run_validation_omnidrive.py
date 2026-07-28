"""Local validation script for OmniDrive v. Ventura Motors case study."""

import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"), override=True)

from google.genai import types
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.memory.memory_entry import MemoryEntry
from ediscovery_review_assistant.config import config
from ediscovery_review_assistant.tools.hybrid_memory import HybridMemoryBankService
from ediscovery_review_assistant.agent import root_agent
from ediscovery_review_assistant.tools.ingestion import parse_document
from ediscovery_review_assistant.tools.gcs_utils import upload_to_gcs, write_ingestion_manifest
import hashlib

# Configure logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout, 
                    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

# New case study files mapping (filename, type, custodian)
SAMPLE_FILES = [
    ("doc1_resignation_slack.txt", "chat_log", "Elena Rostova"),
    ("doc2_competitor_offer.eml", "email", "Arthur Vance"),
    ("doc3_privileged_investigation.eml", "email", "Sarah Jenkins"),
    ("doc4_supplier_leak.eml", "email", "Elena Rostova"),
    ("doc5_exit_survey_noise.eml", "email", "HR"),
]

async def ingest_data(memory_service: HybridMemoryBankService, app_name: str, user_id: str):
    logger.info("Starting ingestion of OmniDrive case study data...")
    sample_data_dir = "sample_data/omnidrive_ip_theft"
    
    for filename, file_type, custodian in SAMPLE_FILES:
        filepath = os.path.join(sample_data_dir, filename)
        if not os.path.exists(filepath):
            logger.error(f"Sample file not found: {filepath}")
            continue
            
        # Read raw bytes for GCS upload and hashing
        with open(filepath, "rb") as f:
            raw_bytes = f.read()
        file_hash = hashlib.sha256(raw_bytes).hexdigest()
        content = raw_bytes.decode("utf-8")
        
        # Upload raw to GCS
        try:
             gcs_uri = upload_to_gcs(raw_bytes, filename, custodian)
        except Exception as e:
             logger.error(f"GCS upload failed for validation file {filename}: {e}")
             continue
            
        # Parse document into chunks with metadata
        payloads = parse_document(
            file_content=content,
            file_name=filename,
            file_type=file_type,
            custodian=custodian,
            file_path=gcs_uri
        )
        
        # Convert payloads to MemoryEntry objects
        memories = []
        for p in payloads:
            content_obj = types.Content(parts=[types.Part.from_text(text=p["text_to_embed"])])
            entry = MemoryEntry(content=content_obj, custom_metadata=p["metadata"])
            memories.append(entry)
            
        # Write to memory service
        if memories:
            await memory_service.add_memory(app_name=app_name, user_id=user_id, memories=memories)
            write_ingestion_manifest(filename, custodian, file_hash, "SUCCESS", len(memories))
        else:
            write_ingestion_manifest(filename, custodian, file_hash, "NO_CHUNKS", 0)
            
    logger.info("Ingestion completed successfully.")

async def run_scenario(runner: Runner, user_id: str, session_id: str, scenario_name: str, query: str):
    print(f"\n==================================================")
    print(f"RUNNING SCENARIO: {scenario_name}")
    print(f"Query: '{query}'")
    print(f"==================================================")
    
    query_content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=query)]
    )
    
    events = runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=query_content
    )
    
    full_response = ""
    async for event in events:
        if hasattr(event, "content") and event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    print(part.text, end="", flush=True)
                    full_response += part.text
        elif hasattr(event, "actions") and event.actions:
             pass
             
    print("\n--------------------------------------------------")
    return full_response

async def main():
    app_name = os.getenv("WORKSPACE_ID") or config.agent_engine_id
    user_id = "attorney_user"
    
    # 1. Initialize services
    db_config = {
        "user": config.db_user,
        "password": config.db_password,
        "database": config.db_name,
        "host": config.db_host,
        "port": int(config.db_port)
    }
    
    # Clear database table for clean validation test run
    logger.info("Dropping old document_chunks table for clean validation run...")
    try:
        import asyncpg
        conn = await asyncpg.connect(**db_config)
        await conn.execute("DELETE FROM document_chunks WHERE app_name = $1;", app_name)
        await conn.close()
    except Exception as e:
        logger.warning(f"Failed to clear old validation table: {e}")
               
    memory_service = HybridMemoryBankService(
        db_config=db_config,
        project=config.project_id,
        location=config.location,
        agent_engine_id=config.agent_engine_id
    )
    session_service = InMemorySessionService()
    
    # 2. Ingest sample data
    await ingest_data(memory_service, app_name, user_id)
    
    # 3. Create Runner
    runner = Runner(
        agent=root_agent,
        app_name=app_name,
        session_service=session_service,
        memory_service=memory_service,
        auto_create_session=True
    )
    
    session_id = "validation-session-omnidrive"
    
    # --- Execute Scenarios ---
    
    # Scenario 1: Timeline & Resignation
    await run_scenario(
        runner, user_id, session_id,
        "Scenario 1: Resignation Timeline & IT Flagged Downloads",
        "Detail the timeline of events leading to Arthur Vance's resignation and explain what security concerns were flagged by the IT team."
    )
    
    # Scenario 2: Competitor Offer and Intent
    await run_scenario(
        runner, user_id, session_id,
        "Scenario 2: Competitor Recruitment & Tech Benchmarks Discussions",
        "Did Arthur Vance receive an employment offer from Ventura Motors? What proprietary hardware tech or benchmarks did they discuss?"
    )
    
    # Scenario 3: Privilege Auditing & Work Product Rules
    await run_scenario(
        runner, user_id, session_id,
        "Scenario 3: Legal Privilege & Investigation Directive Auditing",
        "Review all files for attorney-client privilege. Identify any correspondence where legal counsel is involved, legal advice is discussed, or litigation strategies are formulated."
    )
    
    # Scenario 4: The Misappropriation Smoking Gun
    await run_scenario(
        runner, user_id, session_id,
        "Scenario 4: Misappropriation Proof (Supplier Leak)",
        "Is there any direct evidence that Ventura Motors has already misappropriated and integrated OmniDrive's proprietary designs?"
    )
    
    # Scenario 5: Offboarding Logistics & Noise Filtering
    await run_scenario(
        runner, user_id, session_id,
        "Scenario 5: HR Offboarding Logistics (Noise Verification)",
        "What standard exit logistics and forms is Arthur Vance instructed to complete according to the HR department?"
    )

    # Scenario 6: Full Case Summary & Timeline Cross-Referencing (All Files Included)
    await run_scenario(
        runner, user_id, session_id,
        "Scenario 6: Comprehensive Case Timeline & Evidence Synthesis (All Files)",
        "Provide a comprehensive case summary and timeline of the trade secret investigation regarding Dr. Arthur Vance, OmniDrive, and Ventura Motors. Who are the actors, what actions did they take, what evidence of theft exists, and how is the legal department handling the response?"
    )

if __name__ == "__main__":
    asyncio.run(main())
