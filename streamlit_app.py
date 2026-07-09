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
from google.adk.sessions import InMemorySessionService

# We need to make sure our package is in the path if running streamlit directly
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from ediscovery_review_assistant.agent import root_agent
from ediscovery_review_assistant.tools.local_memory import LocalVectorMemoryService
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
    st.session_state.memory_service = LocalVectorMemoryService()
if "session_service" not in st.session_state:
    st.session_state.session_service = InMemorySessionService()
if "messages" not in st.session_state:
    st.session_state.messages = []
if "ingested_files" not in st.session_state:
    st.session_state.ingested_files = {}  # filename -> chunk_count

# --- Helper for Async Ingestion ---
async def ingest_file_async(content: str, filename: str, file_type: str, custodian: str):
    # Parse
    payloads = parse_document(
        file_content=content,
        file_name=filename,
        file_type=file_type,
        custodian=custodian,
        file_path=f"gs://firm-discovery-vault/case-alpha/{custodian}/{filename}"
    )
    
    # Convert to MemoryEntry
    memories = []
    for p in payloads:
        content_obj = types.Content(parts=[types.Part.from_text(text=p["text_to_embed"])])
        entry = MemoryEntry(content=content_obj, custom_metadata=p["metadata"])
        memories.append(entry)
        
    if memories:
        await st.session_state.memory_service.add_memory(
            app_name="ediscovery_review_app",
            user_id="attorney_user",
            memories=memories
        )
    return len(memories)

def ingest_file(content: str, filename: str, file_type: str, custodian: str):
    # Run the async ingestion in a new event loop to avoid Streamlit conflicts
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        chunk_count = loop.run_until_complete(ingest_file_async(content, filename, file_type, custodian))
        return chunk_count
    finally:
        loop.close()

# --- Helper for Async Chat ---
async def run_chat_async(query: str):
    runner = Runner(
        agent=root_agent,
        app_name="ediscovery_review_app",
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
        session_id="streamlit_session",
        new_message=query_content
    )
    
    full_response = ""
    async for event in events:
        if hasattr(event, "content") and event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    yield part.text
                    full_response += part.text
    
    # Save assistant response to chat history (session state)
    st.session_state.messages.append({"role": "assistant", "content": full_response})

# --- UI Layout ---
st.title("⚖️ E-Discovery Document Review Assistant")
st.markdown("Interrogate case documents, analyze evidence, and automatically audit for attorney-client privilege in real time.")

# Sidebar for Ingestion and Status
with st.sidebar:
    st.header("📥 Document Ingestion")
    
    custodian = st.text_input("Custodian Name", value="Elena Rostova", help="The employee whose data silo this is.")
    
    uploaded_files = st.file_uploader(
        "Upload Discovery Files (.eml, .txt)", 
        accept_multiple_files=True,
        help="Upload raw email exports or chat logs."
    )
    
    if st.button("🚀 Process & Ingest Files", use_container_width=True) and uploaded_files:
        with st.status("Processing files...", expanded=True) as status:
            for uploaded_file in uploaded_files:
                filename = uploaded_file.name
                if filename in st.session_state.ingested_files:
                    st.write(f"⚠️ {filename} already ingested. Skipping.")
                    continue
                    
                # Read content
                try:
                    content = uploaded_file.read().decode("utf-8")
                except Exception as e:
                    st.write(f"❌ Failed to read {filename}: {e}")
                    continue
                
                # Determine file type
                file_type = "document"
                if filename.endswith(".eml"):
                    file_type = "email"
                elif "slack" in filename.lower() or "chat" in filename.lower() or "log" in filename.lower():
                    file_type = "chat_log"
                    
                st.write(f"Parsing {filename} ({file_type})...")
                chunk_count = ingest_file(content, filename, file_type, custodian)
                
                if chunk_count > 0:
                    st.session_state.ingested_files[filename] = chunk_count
                    st.write(f"✅ Indexed {filename} ({chunk_count} chunks)")
                else:
                    st.write(f"⚠️ {filename} yielded no chunks.")
            status.update(label="Ingestion complete!", state="complete", expanded=False)
            st.rerun()

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
        
    if st.button("🗑️ Reset Memory Bank", type="secondary", use_container_width=True):
        st.session_state.memory_service = LocalVectorMemoryService()
        st.session_state.session_service = InMemorySessionService()
        st.session_state.ingested_files = {}
        st.session_state.messages = []
        st.toast("Memory bank and chat history reset!")
        st.rerun()

# Main Chat Interface
# Display chat messages from history on app rerun
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        # Highlight privilege warning if present
        content = message["content"]
        if "POTENTIAL ATTORNEY-CLIENT PRIVILEGE" in content:
            st.warning(content)
        else:
            st.markdown(content)

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
            full_response = ""
            async for text in run_chat_async(prompt):
                full_response += text
                # We can't easily update the warning style dynamically during streaming in a clean way,
                # so we stream as markdown, and then if it has privilege, we can render it as warning at the end.
                response_placeholder.markdown(full_response + "▌")
            
            # Final render
            if "POTENTIAL ATTORNEY-CLIENT PRIVILEGE" in full_response:
                response_placeholder.warning(full_response)
            else:
                response_placeholder.markdown(full_response)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(consume_stream())
        finally:
            loop.close()
            
    st.rerun()
