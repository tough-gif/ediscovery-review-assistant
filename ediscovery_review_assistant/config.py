"""Config module for the E-Discovery Review Assistant."""

import logging
import os
from pathlib import Path
from dotenv import load_dotenv

# Find the .env file in the parent directory
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path, override=True)

# Disable client certificate mTLS to prevent urllib3/pyopenssl conflict on Python 3.13
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"

logger = logging.getLogger(__name__)

class AgentConfig:
    """Configuration for the E-Discovery Agent."""
    use_vertexai: bool = os.getenv("GOOGLE_GENAI_USE_VERTEXAI") == "1"
    project_id: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    location: str = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    agent_engine_id: str = os.getenv("VERTEX_AGENT_ENGINE_ID", "")
    gcs_bucket: str = os.getenv("GOOGLE_CLOUD_STORAGE_BUCKET", "")
    

    
    # Cloud SQL PostgreSQL database configurations
    db_user: str = os.getenv("DB_USER", "app_user")
    db_password: str = os.getenv("DB_PASSWORD", "ediscovery_pass_123")
    db_name: str = os.getenv("DB_NAME", "ediscovery")
    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: str = os.getenv("DB_PORT", "5432")
    
    # Gemini Model Config
    model_name: str = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")
    temperature: float = float(os.getenv("GEMINI_MODEL_TEMPERATURE") or 0.2)

    # Embedding Config
    embedding_model_name: str = os.getenv("EMBEDDING_MODEL_NAME", "text-embedding-005")
    vector_dimensions: int = int(os.getenv("VECTOR_DIMENSIONS") or 768)

    # Local paths
    base_dir: Path = Path(__file__).parent.parent
    sample_data_dir: Path = base_dir / "sample_data"

config = AgentConfig()

# Monkey-patch Vertex AI Reasoning Engine SDK to fix the OTel flush bug.
# In the official SDK, _force_flush_otel calls tracer_provider.force_flush instead of logger_provider.force_flush when flushing logs.
# This prevents logs from being flushed to GCP before Serverless container throttle.
try:
    import asyncio
    import vertexai.preview.reasoning_engines.templates.adk as adk_template
    
    async def patched_force_flush_otel(
        tracing_enabled: bool = False, logging_enabled: bool = False
    ):
        import opentelemetry.trace
        import opentelemetry.sdk.trace
        import opentelemetry._logs
        import opentelemetry.sdk._logs

        coros = []

        if tracing_enabled:
            tracer_provider = opentelemetry.trace.get_tracer_provider()
            if isinstance(tracer_provider, opentelemetry.sdk.trace.TracerProvider):
                coros.append(asyncio.to_thread(tracer_provider.force_flush))

        if logging_enabled:
            logger_provider = opentelemetry._logs.get_logger_provider()
            if isinstance(logger_provider, opentelemetry.sdk._logs.LoggerProvider):
                # Fix: call logger_provider.force_flush instead of tracer_provider.force_flush
                coros.append(asyncio.to_thread(logger_provider.force_flush))

        await asyncio.gather(*coros, return_exceptions=True)

    adk_template._force_flush_otel = patched_force_flush_otel
    logger.info("Successfully applied monkey-patch to vertexai.preview.reasoning_engines.templates.adk._force_flush_otel")
except Exception as patch_ex:
    logger.warning(f"Could not apply monkey-patch to Vertex AI SDK: {patch_ex}")

