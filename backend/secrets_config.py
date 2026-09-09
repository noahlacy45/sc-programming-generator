"""
Loads DB credentials from GCP Secret Manager at runtime.

Secret IDs expected (in the same GCP project the Cloud Run service runs
in): DB_HOST, DB_USER, DB_PASS, DB_NAME_PROD. DB_NAME_PROD (not DB_NAME)
matches the naming already in use by the wellness questionnaire's secrets
in this project.

GCS_BUCKET_NAME is intentionally NOT here -- it's not sensitive, so it
stays a plain Cloud Run env var (set in deployment/deploy.sh).

For local development, set these as plain env vars and they're used
directly -- no GCP credentials or project needed for local testing.
"""

import os

_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
_secret_client = None
_cache = {}


def _get_secret_client():
    global _secret_client
    if _secret_client is None:
        from google.cloud import secretmanager
        _secret_client = secretmanager.SecretManagerServiceClient()
    return _secret_client


def get_secret(secret_id: str) -> str:
    """
    Fetches a secret's latest version from Secret Manager.
    Local dev / explicit override: an env var with the same name wins,
    so you don't need real GCP access to run this locally.
    Not cached across calls, so a rotated secret is picked up without
    a redeploy -- at the cost of one Secret Manager call per lookup.
    """
    env_value = os.environ.get(secret_id)
    if env_value:
        return env_value

    if not _PROJECT_ID:
        raise RuntimeError(
            f"{secret_id} is not set as an env var, and GCP_PROJECT_ID is not "
            "set to look it up in Secret Manager."
        )

    client = _get_secret_client()
    name = f"projects/{_PROJECT_ID}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(name=name)
    return response.payload.data.decode("UTF-8")


def get_db_config() -> dict:
    """Fetches all four DB credentials in one call, for convenience."""
    return {
        "host": get_secret("DB_HOST"),
        "user": get_secret("DB_USER"),
        "password": get_secret("DB_PASS"),
        "database": get_secret("DB_NAME_PROD"),
        "port": int(os.environ.get("DB_PORT", 3306)),
        "connect_timeout": 10,
    }


def get_anthropic_api_key() -> str:
    """Same secret already used by another tool in this project."""
    return get_secret("ANTHROPIC_API_KEY")
