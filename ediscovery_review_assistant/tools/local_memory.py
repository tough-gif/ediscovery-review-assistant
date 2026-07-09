"""Local vector memory service for testing ADK agents without Vertex AI Reasoning Engine."""

import logging
import math
from typing import Mapping, Sequence, Optional
from google import genai
from google.genai import types
from google.adk.memory import BaseMemoryService
from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry

# We import Session for type hints, but we don't implement session memory yet.
from google.adk.sessions import Session

logger = logging.getLogger(__name__)

class LocalVectorMemoryService(BaseMemoryService):
    """A local in-memory vector database that uses google-genai to generate embeddings
    and performs cosine similarity for search.
    """
    
    def __init__(self, embedding_model: str = "text-embedding-005"):
        super().__init__()
        self.embedding_model = embedding_model
        self.client = genai.Client()
        # Storage: list of dicts: {"entry": MemoryEntry, "embedding": list[float]}
        self.store = []
        logger.info(f"Initialized LocalVectorMemoryService with model {self.embedding_model}")

    def _get_text_from_content(self, content: types.Content) -> str:
        """Helper to extract plain text from types.Content."""
        text_parts = []
        if hasattr(content, "parts") and content.parts:
            for part in content.parts:
                if hasattr(part, "text") and part.text:
                    text_parts.append(part.text)
        return " ".join(text_parts)

    def _cosine_similarity(self, v1: list[float], v2: list[float]) -> float:
        """Calculates cosine similarity between two vectors."""
        dot_product = sum(x * y for x, y in zip(v1, v2))
        norm_v1 = math.sqrt(sum(x * x for x in v1))
        norm_v2 = math.sqrt(sum(x * x for x in v2))
        if not norm_v1 or not norm_v2:
            return 0.0
        return dot_product / (norm_v1 * norm_v2)

    async def add_session_to_memory(self, session: Session) -> None:
        """Not implemented for local mock."""
        pass

    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: Sequence[MemoryEntry],
        custom_metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Generates embeddings and adds memories to the local store."""
        logger.info(f"Adding {len(memories)} memories to local store...")
        for entry in memories:
            text = self._get_text_from_content(entry.content)
            if not text.strip():
                logger.warning("Skipping empty memory entry.")
                continue
                
            try:
                # Generate embedding
                response = self.client.models.embed_content(
                    model=self.embedding_model,
                    contents=text
                )
                embedding = response.embeddings[0].values
                
                self.store.append({
                    "entry": entry,
                    "embedding": embedding
                })
                logger.info(f"Successfully indexed chunk. Content preview: '{text[:50]}...'")
            except Exception as e:
                logger.error(f"Failed to generate embedding for chunk: {e}")
                raise e

    async def search_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        query: str,
    ) -> SearchMemoryResponse:
        """Searches local store using cosine similarity."""
        logger.info(f"Searching memory for query: '{query}'")
        if not self.store:
            logger.info("Memory store is empty.")
            return SearchMemoryResponse(memories=[])
            
        try:
            # Generate embedding for query
            response = self.client.models.embed_content(
                model=self.embedding_model,
                contents=query
            )
            query_embedding = response.embeddings[0].values
        except Exception as e:
            logger.error(f"Failed to generate embedding for query: {e}")
            raise e
            
        # Calculate similarities
        scored_entries = []
        for item in self.store:
            sim = self._cosine_similarity(query_embedding, item["embedding"])
            scored_entries.append((sim, item["entry"]))
            
        # Sort by similarity descending
        scored_entries.sort(key=lambda x: x[0], reverse=True)
        
        # Log top matches
        for idx, (score, entry) in enumerate(scored_entries[:3]):
            text = self._get_text_from_content(entry.content)
            logger.info(f"Match {idx+1}: Score={score:.4f}, Preview='{text[:50]}...'")
            
        # Return top matches (e.g. similarity > 0.5, limit to top 5)
        # We can adjust threshold and limit. For now, let's return top 5.
        top_matches = [entry for score, entry in scored_entries if score > 0.3][:5]
        logger.info(f"Found {len(top_matches)} matching memories (score > 0.3)")
        
        return SearchMemoryResponse(memories=top_matches)
