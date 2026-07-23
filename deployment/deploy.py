# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Deployment script for Regulatory Change Management AI Agent."""

import logging
import os

import vertexai
from absl import app, flags
from dotenv import load_dotenv
from google.api_core import exceptions as google_exceptions
from google.cloud import storage

# Import the root_agent from your current project
from ediscovery_review_assistant.agent import root_agent
from vertexai import agent_engines
from vertexai.preview.reasoning_engines import AdkApp

FLAGS = flags.FLAGS
flags.DEFINE_string("project_id", None, "GCP project ID.")
flags.DEFINE_string("location", None, "GCP location.")
flags.DEFINE_string("bucket", None, "GCP bucket name (without gs:// prefix).")
flags.DEFINE_string("resource_id", None, "ReasoningEngine resource ID.")
flags.DEFINE_string(
    "display_name", "E-Discovery Review Assistant Agent", "Display name for the agent."
)

flags.DEFINE_bool("create", False, "Create a new agent.")
flags.DEFINE_bool("delete", False, "Delete an existing agent.")
flags.mark_bool_flags_as_mutual_exclusive(["create", "delete"])

# Updated to match your project name. Adjust version if necessary.
AGENT_WHL_FILE = "dist/ediscovery_review_assistant-0.1.0-py3-none-any.whl"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def setup_staging_bucket(
    project_id: str, location: str, bucket_name: str
) -> str:
    """Checks if the staging bucket exists, creates it if not."""
    storage_client = storage.Client(project=project_id)
    try:
        bucket = storage_client.lookup_bucket(bucket_name)
        if bucket:
            logger.info("Staging bucket gs://%s already exists.", bucket_name)
        else:
            logger.info("Staging bucket gs://%s not found. Creating...", bucket_name)
            new_bucket = storage_client.create_bucket(
                bucket_name, project=project_id, location=location
            )
            logger.info(
                "Successfully created staging bucket gs://%s in %s.",
                new_bucket.name,
                location,
            )
            new_bucket.iam_configuration.uniform_bucket_level_access_enabled = True
            new_bucket.patch()
            logger.info("Enabled uniform bucket-level access for gs://%s.", new_bucket.name)

    except google_exceptions.Forbidden as e:
        logger.error(
            "Permission denied error for bucket gs://%s. "
            "Ensure the service account has 'Storage Admin' role. Error: %s",
            bucket_name, e,
        )
        raise
    except google_exceptions.ClientError as e:
        logger.error("Failed to create or access bucket gs://%s. Error: %s", bucket_name, e)
        raise

    return f"gs://{bucket_name}"


def make_memory_service():
    """Builder to instantiate Custom Hybrid Memory Service in Vector-Only mode in cloud container."""
    import os
    from ediscovery_review_assistant.tools.hybrid_memory import HybridMemoryBankService
    
    project = os.getenv("AIP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("AIP_LOCATION", "us-central1")
    engine_id = os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID") or os.getenv("VERTEX_AGENT_ENGINE_ID")
    
    return HybridMemoryBankService(
        db_config=None,
        project=project,
        location=location,
        agent_engine_id=engine_id
    )


def create(env_vars: dict[str, str]) -> None:
    """Creates and deploys the agent."""
    adk_app = AdkApp(agent=root_agent, memory_service_builder=make_memory_service)

    if not os.path.exists(AGENT_WHL_FILE):
        logger.error("Agent wheel file not found at: %s", AGENT_WHL_FILE)
        raise FileNotFoundError(f"Agent wheel file not found: {AGENT_WHL_FILE}")

    logger.info("Using agent wheel file: %s", AGENT_WHL_FILE)

    remote_agent = agent_engines.create(
        adk_app,
        requirements=[AGENT_WHL_FILE],
        extra_packages=[AGENT_WHL_FILE],
        env_vars=env_vars,
        display_name=FLAGS.display_name,
    )
    logger.info("Successfully created agent: %s", remote_agent.resource_name)
    
    # Write the engine ID to file for build/deploy pipelines to consume
    try:
        engine_id = remote_agent.resource_name.split("/")[-1]
        with open("deployment/new_engine_id.txt", "w") as f:
            f.write(engine_id)
        logger.info("Saved engine ID to deployment/new_engine_id.txt")
    except Exception as e:
        logger.warning("Failed to save engine ID to file: %s", e)


def delete(resource_id: str) -> None:
    """Deletes the specified agent."""
    logger.info("Attempting to delete agent: %s", resource_id)
    try:
        remote_agent = agent_engines.get(resource_id)
        remote_agent.delete(force=True)
        logger.info("Successfully deleted remote agent: %s", resource_id)
    except google_exceptions.NotFound:
        logger.error("Agent with resource ID %s not found.", resource_id)
    except Exception as e:
        logger.error("An error occurred while deleting agent %s: %s", resource_id, e)


def list_agents() -> None:
    remote_agents = agent_engines.list()
    template = """
{agent.name} ("{agent.display_name}")
- Create time: {agent.create_time}
- Update time: {agent.update_time}
"""
    remote_agents_string = "\n".join(
        template.format(agent=agent) for agent in remote_agents
    )
    logger.info("All remote agents:\n%s", remote_agents_string)


def collect_env_vars() -> dict[str, str]:
    """Collects and filters environment variables for the Regulatory Change Management Agent."""
    env_vars = {}
    
    # Environment variables for E-Discovery Agent
    env_var_keys = [
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GEMINI_MODEL_NAME",
        "GEMINI_MODEL_TEMPERATURE",
        "EMBEDDING_MODEL_NAME",
        "VECTOR_DIMENSIONS",
        "VERTEX_AGENT_ENGINE_ID",
        "GOOGLE_CLOUD_STORAGE_BUCKET",
        "OTEL_SEMCONV_STABILITY_OPT_IN",
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
    ]

    skipped_vars: list[str] = []
    for key in env_var_keys:
        value = os.getenv(key)
        if value and value.strip():
            env_vars[key] = value
        else:
            skipped_vars.append(key)

    if skipped_vars:
        logger.info("Skipped empty/None/whitespace environment variables: %s", skipped_vars)

    logger.info("Environment variables to be passed to agent: %s", list(env_vars.keys()))
    return env_vars


def validate_inputs(
    project_id: str | None, location: str | None, bucket_name: str | None
) -> None:
    """Validates input arguments."""
    if not project_id:
        raise app.UsageError("Missing required GCP Project ID.")
    if not location:
        raise app.UsageError("Missing required GCP Location.")
    if not bucket_name:
        raise app.UsageError("Missing required GCS Bucket Name.")
    if not FLAGS.create and not FLAGS.delete:
        raise app.UsageError("You must specify either --create or --delete flag.")
    if FLAGS.delete and not FLAGS.resource_id:
        raise app.UsageError("--resource_id is required when using the --delete flag.")


def main(argv: list[str]) -> None:
    """Main execution function."""
    load_dotenv()

    project_id = FLAGS.project_id if FLAGS.project_id else os.getenv("GOOGLE_CLOUD_PROJECT")
    location = FLAGS.location if FLAGS.location else os.getenv("GOOGLE_CLOUD_LOCATION")
    
    default_bucket_name = f"{project_id}-adk-staging" if project_id else None
    bucket_name = FLAGS.bucket if FLAGS.bucket else os.getenv("GOOGLE_CLOUD_STORAGE_BUCKET", default_bucket_name)

    logger.info("Using PROJECT: %s", project_id)
    logger.info("Using LOCATION: %s", location)
    logger.info("Using BUCKET NAME: %s", bucket_name)

    validate_inputs(project_id, location, bucket_name)
    env_vars = collect_env_vars()

    try:
        staging_bucket_uri = None
        if FLAGS.create:
            staging_bucket_uri = setup_staging_bucket(project_id, location, bucket_name)

        vertexai.init(
            project=project_id,
            location=location,
            staging_bucket=staging_bucket_uri,
        )

        if FLAGS.create:
            create(env_vars)
        elif FLAGS.delete:
            delete(FLAGS.resource_id)

    except google_exceptions.Forbidden as e:
        logger.error("Permission Error: Ensure the service account has necessary permissions.\nDetails: %s", e)
    except FileNotFoundError as e:
        logger.error("\nFile Error: %s", e)
        logger.error("Please ensure you have built the wheel file (e.g., using `uv build` or `poetry build`).")
    except Exception as e:
        logger.error("An unexpected error occurred: %s", e)
        logger.exception("Unhandled exception in main:")


if __name__ == "__main__":
    app.run(main)