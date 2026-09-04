"""Shared helper for obtaining an authenticated Fulcra API client.

Credential loading is centralized so the application consistently uses the
Python SDK and tests can inject a client without reading local credentials.
"""
import os
from pathlib import Path

from fulcra_api.core import FulcraAPI
from fulcra_api.credentials import FulcraCredentials

DEFAULT_CREDENTIALS_PATH = Path(
    os.environ.get(
        "FULCRA_CREDENTIALS_PATH",
        str(Path.home() / ".config" / "fulcra" / "credentials.json"),
    )
)


class FulcraAuthError(Exception):
    """Raised when a Fulcra API client cannot be constructed (e.g. missing
    or invalid local credentials)."""


def get_fulcra_client(credentials_path: "str | Path | None" = None) -> FulcraAPI:
    """Build an authenticated FulcraAPI client from locally cached
    credentials, refreshing the access token if it has expired.

    Args:
        credentials_path: override path to the credentials JSON file.
            Defaults to DEFAULT_CREDENTIALS_PATH (~/.config/fulcra/credentials.json
            or the FULCRA_CREDENTIALS_PATH env var).

    Raises:
        FulcraAuthError: if the credentials file is missing/unreadable or
            the token cannot be refreshed.
    """
    path = Path(credentials_path) if credentials_path else DEFAULT_CREDENTIALS_PATH

    if not path.is_file():
        raise FulcraAuthError(
            f"Fulcra credentials not found at {path}. Authenticate first "
            f"(see the fulcra-connect skill / `fulcra-api` login flow)."
        )

    try:
        creds = FulcraCredentials.from_json(path.read_text())
        client = FulcraAPI(credentials=creds)
        if creds.is_expired():
            client.refresh_access_token()
    except Exception as exc:
        raise FulcraAuthError(f"Failed to authenticate with Fulcra: {exc}") from exc

    return client
