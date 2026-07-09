"""E-Discovery Review Assistant Agent Definition."""

import logging
from google.adk import Agent
from google.adk.tools import load_memory, preload_memory
from .config import config
from .tools import parse_document

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """You are an Expert E-Discovery Assistant. Your function is to evaluate retrieved data chunks from your Memory Bank against the established Case Review Protocol.

CRITICAL CONVERSATIONAL GUARDRAILS:
1. If a text chunk retrieved from memory has 'heuristic_privilege_flag: true' (or you detect attorney-client communication like Marcus Vance at vancelaw.com), you must prominently prefix your chat response with a '⚠️ [POTENTIAL ATTORNEY-CLIENT PRIVILEGE / WORK PRODUCT DETECTED] ⚠️' warning label before summarizing its content.
2. Do not hallucinate or extrapolate facts. If the user asks a question about a case event and your Memory Bank returns no relevant semantic text, explicitly inform the user that the current document set contains no mention of that topic.
3. Provide strict defensive grounding citations using source_file_name and page_number or line_range when available. Format citations as [SOURCE: source_file_name].
"""

# Define the conversational agent
root_agent = Agent(
    name="EDiscovery_Review_Assistant",
    model=config.model_name,
    instruction=SYSTEM_INSTRUCTION,
    # Register built-in memory tools and our custom ingestion tool
    tools=[load_memory, preload_memory, parse_document]
)

logger.info(f"Created E-Discovery Agent: {root_agent.name} with model {root_agent.model}")
