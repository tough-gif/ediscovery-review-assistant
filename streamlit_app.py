"""Streamlit Frontend for E-Discovery Document Review Assistant."""

import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
load_dotenv()

import streamlit as st
from google.genai import types
from google.adk import Runner
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService
from ediscovery_review_assistant.config import config
from ediscovery_review_assistant.tools.hybrid_memory import HybridMemoryBankService
from ediscovery_review_assistant.tools.gcs_utils import upload_to_gcs, write_ingestion_manifest, write_chat_audit_log, write_system_audit_log
import hashlib
import uuid
import asyncpg
import json

def download_gcs_json(blob_name: str) -> list:
    """Downloads and parses a JSON log from GCS, returning an empty list if not found."""
    if not config.gcs_bucket:
        return []
    try:
        from ediscovery_review_assistant.tools.gcs_utils import get_storage_client
        client = get_storage_client()
        bucket = client.bucket(config.gcs_bucket)
        blob = bucket.blob(blob_name)
        if blob.exists():
            return json.loads(blob.download_as_text())
    except Exception as e:
        logger.error(f"Error fetching log {blob_name} from GCS: {e}")
    return []

def load_active_workspace() -> str:
    config_path = "workspace_config.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
                val = data.get("active_workspace_id")
                if val:
                    return val
        except Exception:
            pass
    # Fallback to the environment-configured Engine ID if available
    val = os.getenv("VERTEX_AGENT_ENGINE_ID")
    if val:
        return val
    new_id = f"ediscovery_review_app-{uuid.uuid4()}"
    save_active_workspace(new_id)
    return new_id

def save_active_workspace(workspace_id: str):
    config_path = "workspace_config.json"
    try:
        with open(config_path, "w") as f:
            json.dump({"active_workspace_id": workspace_id}, f)
    except Exception as e:
        logging.error(f"Failed to write workspace config: {e}")

# We need to make sure our package is in the path if running streamlit directly
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from ediscovery_review_assistant.agent import root_agent
from ediscovery_review_assistant.tools.ingestion import parse_document

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="E-Discovery Review Assistant",
    page_icon="⚖️",
    layout="wide"
)

# --- State Management ---
if "memory_service" not in st.session_state:
    db_config = {
        "user": config.db_user,
        "password": config.db_password,
        "database": config.db_name,
        "host": config.db_host,
        "port": int(config.db_port)
    }
    st.session_state.memory_service = HybridMemoryBankService(
        db_config=db_config,
        project=config.project_id,
        location=config.location,
        agent_engine_id=config.agent_engine_id
    )
if "session_service" not in st.session_state:
    st.session_state.session_service = VertexAiSessionService(
        project=config.project_id,
        location=config.location,
        agent_engine_id=config.agent_engine_id
    )
if "messages" not in st.session_state:
    st.session_state.messages = []
if "ingested_files" not in st.session_state:
    st.session_state.ingested_files = {}  # filename -> chunk_count
if "session_id" not in st.session_state:
    st.session_state.session_id = f"streamlit-session-{uuid.uuid4()}"
if "app_name" not in st.session_state:
    st.session_state.app_name = load_active_workspace()

def render_ingestion_flow(placeholder, file_states):
    svg_spinner = (
        '<span style="display: inline-block; vertical-align: middle; margin-right: 6px;">'
        '<svg width="12" height="12" viewBox="0 0 50 50" style="animation: spin_loader 1.2s linear infinite; display: block;">'
        '<circle cx="25" cy="25" r="20" fill="none" stroke="#0066cc" stroke-width="6" stroke-linecap="round" stroke-dasharray="80, 150"></circle>'
        '</svg></span>'
        '<style>@keyframes spin_loader { 100% { transform: rotate(360deg); } }</style>'
    )
    
    lines = ["### 📥 Ingestion Pipeline Flow\n"]
    
    for filename, states in file_states.items():
        if states["archive"] == "waiting":
            continue
            
        # 1. If complete, write clean Markdown line (absolutely safe from HTML breaking)
        if states["index"] == "success":
            lines.append(f"✅ **{filename}** (Indexed **{states['chunks']}** chunks)")
            continue
            
        # 2. If skipped, write a single clean skipped line and skip individual steps
        if states["index"] == "skipped":
            lines.append(f"⏭️ **{filename}** (Skipped: {states['error_msg']})")
            continue
            
        # 2. If running, write file header in Markdown
        lines.append(f"📄 **{filename}**")
        
        # 3. Format active step in HTML/SVG
        def format_step(step_name, status, details=""):
            if status == "waiting" or status == "success":
                return ""
            if status == "error":
                return f'<div style="margin-left: 18px; color: #d32f2f; font-weight: bold; display: flex; align-items: center; height: 22px;">❌&nbsp;{step_name} (Failed)</div>'
            elif status == "running":
                return f'<div style="margin-left: 18px; color: #0066cc; font-weight: bold; display: flex; align-items: center; height: 22px;">{svg_spinner}&nbsp;{step_name}...</div>'
            elif status == "skipped":
                return f'<div style="margin-left: 18px; color: #888888; font-style: italic; display: flex; align-items: center; height: 22px;">⏭️&nbsp;{step_name} (Skipped)</div>'
            return ""

        step1 = format_step("Archive raw backup to GCS", states["archive"])
        if step1: lines.append(step1)
        
        step2 = format_step("Extract text & chunk document", states["parse"])
        if step2: lines.append(step2)
        
        chunk_info = f" (Indexed <b>{states['chunks']} chunks</b>)" if states["chunks"] > 0 else ""
        step3 = format_step("Index database & memory bank", states["index"], chunk_info)
        if step3: lines.append(step3)
        
        if states["error_msg"]:
            lines.append(f'<div style="margin-left: 18px; color: #d32f2f; font-style: italic; font-size: 0.85em; margin-top: 3px;">⚠️ Error: {states["error_msg"]}</div>')
            
        lines.append("") # empty line spacing
        
    placeholder.markdown("\n\n".join(lines), unsafe_allow_html=True)

# --- Helper for Async Ingestion ---
async def ingest_file_async(content: str, filename: str, file_type: str, custodian: str, file_path: str):
    # Parse
    payloads = parse_document(
        file_content=content,
        file_name=filename,
        file_type=file_type,
        custodian=custodian,
        file_path=file_path
    )
    
    # Convert to MemoryEntry
    memories = []
    for p in payloads:
        content_obj = types.Content(parts=[types.Part.from_text(text=p["text_to_embed"])])
        entry = MemoryEntry(content=content_obj, custom_metadata=p["metadata"])
        memories.append(entry)
        
    if memories:
        await st.session_state.memory_service.add_memory(
            app_name=st.session_state.app_name,
            user_id="attorney_user",
            memories=memories
        )
    return len(memories)

def ingest_file(content: str, filename: str, file_type: str, custodian: str, file_path: str):
    # Run the async ingestion in a new event loop to avoid Streamlit conflicts
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        chunk_count = loop.run_until_complete(ingest_file_async(content, filename, file_type, custodian, file_path))
        return chunk_count
    finally:
        loop.close()

# --- Helper for Async Chat ---
async def run_chat_async(query: str):
    use_cloud = os.getenv("USE_CLOUD_AGENT", "false").lower() == "true"
    
    if use_cloud:
        # Route to Cloud deployed Reasoning Engine
        from vertexai.preview.reasoning_engines import ReasoningEngine
        import vertexai
        
        # Initialize Vertex AI
        vertexai.init(project=config.project_id, location=config.location)
        
        # Connect to engine
        engine_resource_name = f"projects/{config.project_id}/locations/{config.location}/reasoningEngines/{config.agent_engine_id}"
        logger.info(f"Routing chat query to Cloud Agent Engine: {engine_resource_name}")
        
        engine = ReasoningEngine(engine_resource_name)
        
        # Step 1: Create session in the cloud session service using the Session Service client
        try:
            await st.session_state.session_service.create_session(
                app_name=config.agent_engine_id,
                user_id="attorney_user",
                session_id=st.session_state.session_id
            )
        except Exception as e:
            logger.info(f"Cloud session registration notice (likely already exists): {e}")
            
        # Step 2: Stream query response from the cloud engine using direct stream request
        response_stream = engine.execution_api_client.stream_query_reasoning_engine(
            request={
                "name": engine.resource_name,
                "class_method": "stream_query",
                "input": {
                    "message": query,
                    "user_id": "attorney_user",
                    "session_id": st.session_state.session_id
                }
            }
        )
        
        full_response = ""
        # Parse HttpBody stream chunks
        for chunk in response_stream:
            if hasattr(chunk, "data") and chunk.data:
                try:
                    event_data = json.loads(chunk.data.decode("utf-8"))
                    if "content" in event_data:
                        parts = event_data["content"].get("parts", [])
                        for part in parts:
                            text = part.get("text", "")
                            if text:
                                yield text
                                full_response += text
                except Exception as e:
                    logger.error(f"Error decoding client event chunk: {e}")
                            
        st.session_state.messages.append({"role": "assistant", "content": full_response})
        
    else:
        # Route to local Runner (default)
        runner = Runner(
            agent=root_agent,
            app_name=st.session_state.app_name,
            session_service=st.session_state.session_service,
            memory_service=st.session_state.memory_service,
            auto_create_session=True
        )
        
        query_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=query)]
        )
        
        events = runner.run_async(
            user_id="attorney_user",
            session_id=st.session_state.session_id,
            new_message=query_content
        )
        
        full_response = ""
        async for event in events:
            if hasattr(event, "content") and event.content and event.content.parts:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        yield part.text
                        full_response += part.text
        
        st.session_state.messages.append({"role": "assistant", "content": full_response})

# --- UI Layout ---
st.title("⚖️ E-Discovery Document Review Assistant")
st.markdown("Interrogate case documents, analyze evidence, and automatically audit for attorney-client privilege in real time.")

# Sidebar for Ingestion and Status
with st.sidebar:
    st.header("📥 Document Ingestion")
    
    custodian = st.text_input("Custodian Name", value="Elena Rostova", help="The employee whose data silo this is.")
    
    uploaded_files = st.file_uploader(
        "Upload Discovery Files (.eml, .txt, .pdf)", 
        accept_multiple_files=True,
        help="Upload raw email exports or chat logs."
    )
    
    if st.button("🚀 Process & Ingest Files", use_container_width=True) and uploaded_files:
        with st.status("Processing files...", expanded=True) as status:
            # 1. Initialize states for all files
            file_states = {}
            for f in uploaded_files:
                file_states[f.name] = {
                    "archive": "waiting",
                    "parse": "waiting",
                    "index": "waiting",
                    "chunks": 0,
                    "error_msg": ""
                }
            
            flow_placeholder = st.empty()
            render_ingestion_flow(flow_placeholder, file_states)
            
            # Download manifest to fetch global ingested hashes and names in this workspace
            manifest = download_gcs_json("audit_logs/ingestion_manifest.json")
            existing_hashes = {entry["sha256_hash"] for entry in manifest if entry.get("status") == "SUCCESS" and "sha256_hash" in entry}
            existing_names = {entry["filename"] for entry in manifest if entry.get("status") == "SUCCESS"}
            
            for idx, uploaded_file in enumerate(uploaded_files):
                filename = uploaded_file.name
                raw_bytes = uploaded_file.getvalue()
                file_hash = hashlib.sha256(raw_bytes).hexdigest()
                
                # Check de-duplication constraints (content hash and filename)
                is_duplicate_hash = file_hash in existing_hashes
                is_duplicate_name = filename in existing_names or filename in st.session_state.ingested_files
                
                if is_duplicate_hash or is_duplicate_name:
                    file_states[filename]["archive"] = "skipped"
                    file_states[filename]["parse"] = "skipped"
                    file_states[filename]["index"] = "skipped"
                    
                    reason = "File already exists"
                    file_states[filename]["error_msg"] = reason
                    render_ingestion_flow(flow_placeholder, file_states)
                    continue
                
                # --- Step 1: Archive to GCS ---
                file_states[filename]["archive"] = "running"
                render_ingestion_flow(flow_placeholder, file_states)
                
                try:
                    if filename.lower().endswith(".pdf"):
                        from ediscovery_review_assistant.tools.ingestion import extract_text_from_pdf
                        content = extract_text_from_pdf(raw_bytes)
                    else:
                        content = raw_bytes.decode("utf-8")
                        
                    gcs_uri = upload_to_gcs(raw_bytes, filename, custodian)
                    file_states[filename]["archive"] = "success"
                    render_ingestion_flow(flow_placeholder, file_states)
                except Exception as e:
                    file_states[filename]["archive"] = "error"
                    file_states[filename]["error_msg"] = f"Archive failed: {e}"
                    render_ingestion_flow(flow_placeholder, file_states)
                    continue
                
                # Determine file type
                file_type = "document"
                if filename.lower().endswith(".eml"):
                    file_type = "email"
                elif filename.lower().endswith(".pdf"):
                    file_type = "pdf"
                elif "slack" in filename.lower() or "chat" in filename.lower() or "log" in filename.lower():
                    file_type = "chat_log"
                
                # --- Step 2: Extract & Chunk text ---
                file_states[filename]["parse"] = "running"
                render_ingestion_flow(flow_placeholder, file_states)
                
                try:
                    payloads = parse_document(
                        file_content=content,
                        file_name=filename,
                        file_type=file_type,
                        custodian=custodian,
                        file_path=gcs_uri
                    )
                    memories = []
                    for p in payloads:
                        content_obj = types.Content(parts=[types.Part.from_text(text=p["text_to_embed"])])
                        entry = MemoryEntry(content=content_obj, custom_metadata=p["metadata"])
                        memories.append(entry)
                    file_states[filename]["parse"] = "success"
                    render_ingestion_flow(flow_placeholder, file_states)
                except Exception as e:
                    file_states[filename]["parse"] = "error"
                    file_states[filename]["error_msg"] = f"Parsing failed: {e}"
                    render_ingestion_flow(flow_placeholder, file_states)
                    continue
                
                # --- Step 3: Index database & memory bank ---
                chunk_count = len(memories)
                file_states[filename]["index"] = "running"
                render_ingestion_flow(flow_placeholder, file_states)
                
                if chunk_count > 0:
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(
                                st.session_state.memory_service.add_memory(
                                    app_name=st.session_state.app_name,
                                    user_id="attorney_user",
                                    memories=memories
                                )
                            )
                        finally:
                            loop.close()
                            
                        st.session_state.ingested_files[filename] = chunk_count
                        file_states[filename]["index"] = "success"
                        file_states[filename]["chunks"] = chunk_count
                        render_ingestion_flow(flow_placeholder, file_states)
                        write_ingestion_manifest(filename, custodian, file_hash, "SUCCESS", chunk_count)
                    except Exception as e:
                        file_states[filename]["index"] = "error"
                        file_states[filename]["error_msg"] = f"Indexing failed: {e}"
                        render_ingestion_flow(flow_placeholder, file_states)
                        continue
                else:
                    file_states[filename]["index"] = "skipped"
                    render_ingestion_flow(flow_placeholder, file_states)
                    write_ingestion_manifest(filename, custodian, file_hash, "NO_CHUNKS", 0)
                
            # Rotate session ID and clear history to prevent conversation history bias
            st.session_state.messages = []
            st.session_state.session_id = f"streamlit-session-{uuid.uuid4()}"
            status.update(label="Ingestion complete!", state="complete", expanded=True)



    st.markdown("---")
    st.header("📊 Case Files Memory Bank")
    total_chunks = sum(st.session_state.ingested_files.values())
    st.metric("Total Indexed Chunks", total_chunks)
    
    if st.session_state.ingested_files:
        st.write("**Ingested Files:**")
        for filename, count in st.session_state.ingested_files.items():
            st.markdown(f"- `{filename}` ({count} chunks)")
    else:
        st.info("No documents ingested yet.")
        
    # Reset Memory Bank button removed for production compliance policy

# Main UI Tabs
tab_chat, tab_audit = st.tabs(["💬 Case Investigation", "📋 Compliance & Audit Trail"])

with tab_chat:
    # Display chat messages from history on app rerun
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    # Accept user input
    if prompt := st.chat_input("Ask a question about the case documents..."):
        # Display user message in chat message container
        with st.chat_message("user"):
            st.markdown(prompt)
        # Add user message to chat history
        st.session_state.messages.append({"role": "user", "content": prompt})
    
        # Display assistant response in chat message container
        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            
            # We need to run the async generator in a helper
            async def consume_stream():
                # Show retrieval spinner before first token arrives
                response_placeholder.markdown(
                    '<div style="display: flex; align-items: center; color: #0066cc; font-weight: bold; font-style: italic; margin-bottom: 10px;">'
                    '<span style="display: inline-block; vertical-align: middle; margin-right: 8px;">'
                    '<svg width="14" height="14" viewBox="0 0 50 50" style="animation: spin_loader 1.2s linear infinite; display: block;">'
                    '<circle cx="25" cy="25" r="20" fill="none" stroke="#0066cc" stroke-width="6" stroke-linecap="round" stroke-dasharray="80, 150"></circle>'
                    '</svg></span>'
                    'Analyzing evidence...'
                    '</div>'
                    '<style>@keyframes spin_loader { 100% { transform: rotate(360deg); } }</style>',
                    unsafe_allow_html=True
                )
                
                full_response = ""
                first_chunk = True
                async for text in run_chat_async(prompt):
                    if first_chunk:
                        response_placeholder.empty()
                        first_chunk = False
                    full_response += text
                    # We can't easily update the warning style dynamically during streaming in a clean way,
                    # so we stream as markdown, and then if it has privilege, we can render it as warning at the end.
                    response_placeholder.markdown(full_response + "▌")
                
                # Final render
                response_placeholder.markdown(full_response)
                
                # Log chat query audit entry
                files_cited = [f for f in st.session_state.ingested_files.keys() if f.lower() in full_response.lower() or f.lower() in prompt.lower()]
                write_chat_audit_log(
                    session_id=st.session_state.session_id,
                    query=prompt,
                    response=full_response,
                    files_cited=files_cited
                )
    
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(consume_stream())
            finally:
                loop.close()
                
        st.rerun()

with tab_audit:
    st.header("📋 Compliance & Audit Trail")
    
    # Fetch logs from GCS
    with st.spinner("Downloading audit records from Cloud Storage..."):
        ingestion_data = download_gcs_json("audit_logs/ingestion_manifest.json")
        chat_data = download_gcs_json("audit_logs/chat_history.json")
        
    st.divider()
    
    # --- Compliance Summary Metrics ---
    st.subheader("📊 Compliance Summary")
    col1, col2 = st.columns(2)
    with col1:
        total_files = len(ingestion_data)
        st.metric("Total Files Audited", total_files)
    with col2:
        unique_custodians = len(set(d.get("custodian", "") for d in ingestion_data)) if ingestion_data else 0
        st.metric("Active Custodians", unique_custodians)
        
    st.divider()
    
    # --- Ingestion Records Ledger ---
    st.subheader("⛓️ Chain of Custody Ingestion Ledger")
    if ingestion_data:
        import pandas as pd
        df_ingest = pd.DataFrame(ingestion_data)
        
        # Prepare display dataframe (hiding cryptographic hashes)
        df_display = df_ingest.drop(columns=["sha256_hash"], errors="ignore")
        df_display.rename(columns={
            "timestamp": "Timestamp (UTC)",
            "filename": "File Name",
            "custodian": "Custodian",
            "status": "Status",
            "chunk_count": "Chunks"
        }, inplace=True)
        df_display.sort_values(by="Timestamp (UTC)", ascending=False, inplace=True)
        st.dataframe(df_display, use_container_width=True, hide_index=True)
        
        # CSV conversion logic removed as download button is deleted
    else:
        st.info("No file ingestion events recorded yet.")
        
    st.divider()
    
    # --- Chat Auditing & Counsel Transcripts ---
    st.subheader("💬 Counsel Chat & AI Citations Audit Trail")
    if chat_data:
        import pandas as pd
        df_chat = pd.DataFrame(chat_data)
        df_chat.rename(columns={
            "timestamp": "Timestamp (UTC)",
            "session_id": "Session ID",
            "query": "Reviewer Query",
            "response": "AI Response",
            "files_cited": "Cited Documents"
        }, inplace=True)
        df_chat.sort_values(by="Timestamp (UTC)", ascending=False, inplace=True)
        
        # Build friendly display names for the sessions descending by latest activity
        session_mapping = {}
        df_chronological = df_chat.sort_values(by="Timestamp (UTC)")
        grouped_sessions = list(df_chronological.groupby("Session ID", sort=False))
        
        # Sort the sessions list by the newest timestamp in each group descending (latest first)
        grouped_sessions.sort(key=lambda g: g[1]["Timestamp (UTC)"].max(), reverse=True)
        
        for session_id, group in grouped_sessions:
            first_turn = group.iloc[0]
            timestamp_str = first_turn["Timestamp (UTC)"]
            
            # Format timestamp nicely (e.g., "Jul 21, 06:30 AM")
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                time_label = dt.strftime("%b %d, %I:%M %p")
            except Exception:
                time_label = timestamp_str[5:16].replace("T", " ")
                
            first_query = first_turn["Reviewer Query"]
            query_preview = (first_query[:35] + "...") if len(first_query) > 35 else first_query
            
            display_name = f"{query_preview} ({time_label})"
            session_mapping[display_name] = session_id
            
        selected_display = st.selectbox("📂 Select Chat Session to view Transcript", list(session_mapping.keys()))
        selected_session = session_mapping.get(selected_display)
        
        if selected_session:
            session_turns = df_chat[df_chat["Session ID"] == selected_session].sort_values(by="Timestamp (UTC)")
            
            # Transcript display section
            
            # Collapse actual chat details under an expander so it is not shown directly
            with st.expander("🔍 View Complete Chat Transcript", expanded=False):
                st.markdown("#### 💬 Transcript Review Room")
                for _, turn in session_turns.iterrows():
                    st.markdown(f"**👤 Counsel Query ({turn['Timestamp (UTC)']}):**")
                    st.write(turn["Reviewer Query"])
                    st.markdown(f"**🤖 AI Agent Response:**")
                    st.write(turn["AI Response"])
                    if turn["Cited Documents"]:
                        st.markdown(f"📑 *Cited Files:* {', '.join(f'`{f}`' for f in turn['Cited Documents'])}")
                    st.markdown("---")
    else:
        st.info("No chat history queries recorded yet.")
        
    # Administrative Operations Log section removed because Reset function is restricted
