"""Local validation script for E-Discovery Review Assistant."""

import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
load_dotenv()

from google.genai import types
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.memory.memory_entry import MemoryEntry

from ediscovery_review_assistant.agent import root_agent
from ediscovery_review_assistant.tools.local_memory import LocalVectorMemoryService
from ediscovery_review_assistant.tools.ingestion import parse_document

# Configure logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout, 
                    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

# Sample files mapping (filename, type, custodian)
SAMPLE_FILES = [
    ("doc1_hot_slack_logs.txt", "chat_log", "Elena Rostova"),
    ("doc2_privileged_lease.eml", "email", "Sarah Jenkins"),
    ("doc3_mixed_liability_chain.eml", "email", "David Vance"),
    ("doc4_collusion_pricing.eml", "email", "David Vance"),
    ("doc5_hr_noise_picnic.eml", "email", "HR"),
]

async def ingest_data(memory_service: LocalVectorMemoryService, app_name: str, user_id: str):
    logger.info("Starting ingestion of sample data...")
    sample_data_dir = "sample_data"
    
    for filename, file_type, custodian in SAMPLE_FILES:
        filepath = os.path.join(sample_data_dir, filename)
        if not os.path.exists(filepath):
            logger.error(f"Sample file not found: {filepath}")
            continue
            
        with open(filepath, "r") as f:
            content = f.read()
            
        # Parse document into chunks with metadata
        payloads = parse_document(
            file_content=content,
            file_name=filename,
            file_type=file_type,
            custodian=custodian,
            file_path=f"gs://firm-discovery-vault/case-alpha/{custodian}/{filename}"
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
        # Check if event has content (response from model)
        if hasattr(event, "content") and event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    print(part.text, end="", flush=True)
                    full_response += part.text
        # Or if it is a tool call/response we might want to log it if debug is enabled
        elif hasattr(event, "actions") and event.actions:
             pass # can log actions here if needed
             
    print("\n--------------------------------------------------")
    return full_response

async def main():
    app_name = "ediscovery_review_app"
    user_id = "attorney_user"
    
    # 1. Initialize services
    memory_service = LocalVectorMemoryService()
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
    
    # 4. Run Scenarios
    # Scenario 1: Smoking Gun
    await run_scenario(
        runner=runner,
        user_id=user_id,
        session_id="session_scenario_1",
        scenario_name="Scenario 1: The 'Smoking Gun' & Metadata Trace",
        query="Are there any conversations where David Vance told engineering to suppress software updates or bypass alarms?"
    )
    
    # Scenario 2: Privilege Guardrail
    await run_scenario(
        runner=runner,
        user_id=user_id,
        session_id="session_scenario_2",
        scenario_name="Scenario 2: Conversational Privilege Guardrail",
        query="Summarize the liability assessment regarding the Oakridge battery fire."
    )
    
    # Scenario 3: Contextual Filter / False Positive
    await run_scenario(
        runner=runner,
        user_id=user_id,
        session_id="session_scenario_3",
        scenario_name="Scenario 3: Contextual Filter / False Positive",
        query="Give me all documents mentioning Helios safety baselines and heat issues."
    )

if __name__ == "__main__":
    asyncio.run(main())
