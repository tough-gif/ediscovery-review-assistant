"""E-Discovery Review Assistant Agent Definition."""

import logging
from google.adk import Agent
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.adk.tools._memory_entry_utils import extract_text
from .config import config

logger = logging.getLogger(__name__)

class CustomPreloadMemoryTool(BaseTool):
    """Custom pre-run tool to load case documents and bind metadata parameters to content blocks."""
    def __init__(self):
        super().__init__(name='preload_memory', description='preload_memory')

    async def process_llm_request(
        self,
        *,
        tool_context: ToolContext,
        llm_request: any,
    ) -> None:
        user_content = tool_context.user_content
        if not user_content or not user_content.parts or not user_content.parts[0].text:
            return

        user_query = user_content.parts[0].text
        try:
            response = await tool_context.search_memory(user_query)
        except Exception as e:
            logger.warning(f"Failed to preload memory: {e}")
            return

        if not response or not response.memories:
            return

        memory_blocks = []
        for memory in response.memories:
            text = extract_text(memory)
            if not text:
                continue
            
            # Form metadata attributes directly into context headers
            meta = memory.custom_metadata or {}
            source_file = meta.get("source_file_name", "unknown_source")
            custodian = meta.get("custodian", "unknown")
            line_range = meta.get("line_range", "unknown")
            
            block = f"--- CASE DOCUMENT CHUNK (Source File: {source_file}, Custodian: {custodian}, Range: {line_range}) ---\n{text}\n"
            memory_blocks.append(block)

        if not memory_blocks:
            return

        if not llm_request.contents:
            return
        
        last_msg = llm_request.contents[-1]
        if last_msg.role != "user":
            return
            
        full_memory_text = "\n".join(memory_blocks)
        context_block = f"""

[RECONSTRUCTED GROUNDING CONTEXT]
The following text chunks are retrieved from the Case Review files. Use them to answer the query:
<RETRIEVED_CASE_DOCUMENTS>
{full_memory_text}
</RETRIEVED_CASE_DOCUMENTS>
"""
        for part in last_msg.parts:
            if part.text:
                part.text += context_block
                break

custom_preload_tool = CustomPreloadMemoryTool()

SYSTEM_INSTRUCTION = """You are an Expert E-Discovery Assistant. Your function is to evaluate retrieved data chunks from your Memory Bank against the established Case Review Protocol.

CRITICAL CONVERSATIONAL GUARDRAILS:
1. Do not hallucinate or extrapolate facts. If the retrieved context chunks from your Memory Bank do not contain the information needed to answer the user's query, explicitly inform the user that the current document set contains no mention of that topic.
2. Dynamic Context Override: The document index updates dynamically. If context chunks are present in <RETRIEVED_CASE_DOCUMENTS> that can answer the query, you MUST prioritize this new context and answer the query fully, completely ignoring any prior statements in the chat history where you claimed there was no mention of the topic.
3. Provide strict defensive grounding citations. You MUST read the 'Source File' parameter from the retrieved chunk header (e.g. 'Source File: filename') and append it as '[SOURCE: filename]' at the end of every sentence or summary point containing facts from that chunk. Do NOT use category names (like 'chat_log' or 'email') for citations.
"""

# Define the conversational agent
root_agent = Agent(
    name="EDiscovery_Review_Assistant",
    model=config.model_name,
    instruction=SYSTEM_INSTRUCTION,
    tools=[custom_preload_tool]
)

logger.info(f"Created E-Discovery Agent: {root_agent.name} with model {root_agent.model}")
