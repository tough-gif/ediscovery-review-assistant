"""Hybrid Memory Service combining Cloud Vector and Cloud SQL (PostgreSQL) Search."""

import json
import hashlib
import logging
from typing import Sequence, Mapping, Optional
import asyncpg

from google.adk.memory import VertexAiMemoryBankService
from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types

logger = logging.getLogger(__name__)

class HybridMemoryBankService(VertexAiMemoryBankService):
    """Memory service that combines Vertex AI Cloud Memory Bank with Cloud SQL PostgreSQL keyword indexing."""

    def __init__(self, db_config: dict, *args, **kwargs):
        """Initializes the service with database configuration connection parameters."""
        super().__init__(*args, **kwargs)
        self.db_config = db_config
        self.schema_initialized = False

    async def _ensure_schema(self) -> None:
        """Ensures database tables are initialized once per instance runtime."""
        if self.schema_initialized:
            return
            
        logger.info("Verifying Cloud SQL PostgreSQL database schema...")
        conn = await asyncpg.connect(**self.db_config)
        try:
            # 1. Create document chunks table with JSONB and tsvector support
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS document_chunks (
                    chunk_id VARCHAR(255) PRIMARY KEY,
                    app_name VARCHAR(255),
                    file_name VARCHAR(255),
                    custodian VARCHAR(255),
                    content TEXT,
                    metadata_json JSONB,
                    tsv_content tsvector
                );
            """)
            
            # 2. Create GIN index on tsvector for full-text searches
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS tsv_idx ON document_chunks USING gin (tsv_content);
            """)

            self.schema_initialized = True
            logger.info("Cloud SQL PostgreSQL database schema and indexes initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL database schema: {e}")
            raise
        finally:
            await conn.close()

    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: Sequence[MemoryEntry],
        custom_metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Writes memories to the cloud Vector store and the Cloud SQL PostgreSQL database."""
        # 1. Cloud Vector ingestion
        await super().add_memory(
            app_name=app_name,
            user_id=user_id,
            memories=memories,
            custom_metadata=custom_metadata
        )

        # 2. Cloud SQL PostgreSQL indexing
        await self._ensure_schema()
        conn = await asyncpg.connect(**self.db_config)
        try:
            async with conn.transaction():
                for entry in memories:
                    fact = entry.content.parts[0].text
                    meta = entry.custom_metadata or {}

                    # Compute unique chunk key
                    file_name = meta.get("source_file_name", "unknown_file")
                    line_range = meta.get("line_range", "unknown_chunk")
                    chunk_id = f"{app_name}#{file_name}#{line_range}"
                    custodian = meta.get("custodian", "unknown")

                    meta_json = json.dumps(meta)
                    # Upsert (ON CONFLICT DO UPDATE) with to_tsvector
                    await conn.execute("""
                        INSERT INTO document_chunks(chunk_id, app_name, file_name, custodian, content, metadata_json, tsv_content)
                        VALUES($1, $2, $3, $4, $5, $6, to_tsvector('english', $5))
                        ON CONFLICT (chunk_id) DO UPDATE 
                        SET app_name = EXCLUDED.app_name,
                            file_name = EXCLUDED.file_name,
                            custodian = EXCLUDED.custodian,
                            content = EXCLUDED.content,
                            metadata_json = EXCLUDED.metadata_json,
                            tsv_content = EXCLUDED.tsv_content;
                    """, chunk_id, app_name, file_name, custodian, fact, meta_json)
        finally:
            await conn.close()
        logger.info(f"Successfully indexed {len(memories)} chunks in Cloud SQL PostgreSQL.")

    async def search_memory(self, *, app_name: str, user_id: str, query: str) -> SearchMemoryResponse:
        """Retrieves and merges concepts from Vertex AI and keywords from Cloud SQL PostgreSQL."""
        # 1. Cloud Semantic Search
        semantic_memories = await self._search_cloud_semantic(app_name, user_id, query)
        logger.info(f"Retrieved {len(semantic_memories)} semantic matches from Cloud.")

        # 2. Cloud SQL keyword Search
        keyword_memories = await self._search_postgres_keyword(query, app_name)
        logger.info(f"Retrieved {len(keyword_memories)} keyword matches from Cloud SQL.")

        # 3. Merge & De-duplicate
        merged_memories = self._merge_memories(semantic_memories, keyword_memories)
        logger.info(f"Merged results into {len(merged_memories)} unique memories.")

        return SearchMemoryResponse(memories=merged_memories)

    async def _search_cloud_semantic(self, app_name: str, user_id: str, query: str) -> list[MemoryEntry]:
        """Queries Vertex AI Memory Bank and extracts custom metadata."""
        api_client = self._get_api_client()
        retrieved_memories_iterator = (
            await api_client.agent_engines.memories.retrieve(
                name='reasoningEngines/' + self._agent_engine_id,
                scope={
                    'app_name': app_name,
                    'user_id': user_id,
                },
                similarity_search_params={
                    'search_query': query,
                },
            )
        )

        memory_events: list[MemoryEntry] = []
        try:
            async for retrieved_memory in retrieved_memories_iterator:
                try:
                    memory = retrieved_memory.memory
                    if memory is None:
                        continue
                    fact = memory.fact
                    if not fact:
                        continue

                    # Extract the custom metadata stored in the Cloud
                    gcp_metadata = getattr(memory, 'metadata', None)
                    custom_metadata = self._parse_gcp_metadata(gcp_meta=gcp_metadata)

                    update_time = memory.update_time
                    memory_events.append(
                        MemoryEntry(
                            author='user',
                            content=types.Content(
                                parts=[types.Part(text=fact)],
                                role='user',
                            ),
                            timestamp=update_time.isoformat() if update_time else None,
                            custom_metadata=custom_metadata
                        )
                    )
                except AttributeError as e:
                    logger.warning('Skipping malformed memory entry: %s', e)
        except Exception:
            logger.exception('Error while iterating cloud memory results.')
        return memory_events

    def _parse_gcp_metadata(self, gcp_meta) -> dict:
        """Helper to parse protobuf-like metadata map to raw dictionary."""
        result = {}
        if not gcp_meta:
            return result
        items = gcp_meta.items() if hasattr(gcp_meta, 'items') else []
        for key, val in items:
            if not isinstance(val, dict):
                if hasattr(val, 'string_value') and val.string_value:
                    result[key] = val.string_value
                elif hasattr(val, 'bool_value') and val.bool_value is not None:
                    result[key] = val.bool_value
                else:
                    result[key] = val
                continue

            if 'string_value' in val and val['string_value'] is not None:
                result[key] = val['string_value']
            elif 'bool_value' in val and val['bool_value'] is not None:
                result[key] = val['bool_value']
            elif 'double_value' in val and val['double_value'] is not None:
                result[key] = val['double_value']
            elif 'number_value' in val and val['number_value'] is not None:
                result[key] = val['number_value']
            else:
                result[key] = val
        return result

    async def _search_postgres_keyword(self, query: str, app_name: str) -> list[MemoryEntry]:
        """Queries the PostgreSQL full-text search index for matches."""
        memories: list[MemoryEntry] = []
        
        # Clean query for FTS matching
        clean_query = query.replace("'", "").replace('"', '').strip()
        if not clean_query:
            return memories
            
        await self._ensure_schema()
        conn = await asyncpg.connect(**self.db_config)
        try:
            rows = await conn.fetch("""
                SELECT chunk_id, content, metadata_json 
                FROM document_chunks 
                WHERE app_name = $2 AND tsv_content @@ websearch_to_tsquery('english', $1)
                LIMIT 10
            """, clean_query, app_name)
            
            for row in rows:
                content = row["content"]
                meta_json = row["metadata_json"]
                
                # Unpack metadata JSON string
                custom_metadata = json.loads(meta_json) if isinstance(meta_json, str) else meta_json
                if not custom_metadata:
                    custom_metadata = {}
                    
                memories.append(
                    MemoryEntry(
                        author='user',
                        content=types.Content(
                            parts=[types.Part(text=content)],
                            role='user'
                        ),
                        custom_metadata=custom_metadata
                    )
                )
        except Exception as e:
            logger.error(f"PostgreSQL FTS query failed: {e}")
        finally:
            await conn.close()
        return memories

    def _merge_memories(self, list_a: list[MemoryEntry], list_b: list[MemoryEntry]) -> list[MemoryEntry]:
        """Merges two lists of memories, de-duplicating by unique chunk keys."""
        merged = []
        seen_keys = set()
        
        for entry in list_a + list_b:
            meta = entry.custom_metadata or {}
            file_name = meta.get("source_file_name", "unknown")
            line_range = meta.get("line_range", "unknown")
            key = f"{file_name}#{line_range}"
            
            # Fallback key using text hash
            if file_name == "unknown" and line_range == "unknown":
                text = entry.content.parts[0].text
                key = hashlib.sha256(text.encode()).hexdigest()
                
            if key not in seen_keys:
                seen_keys.add(key)
                merged.append(entry)
                
        return merged


