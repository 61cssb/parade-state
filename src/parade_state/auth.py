"""Google OAuth authentication utilities."""

import os
from functools import lru_cache
from typing import Optional

from authlib.integrations.starlette_client import OAuth


@lru_cache
def get_oauth() -> OAuth:
    """Get configured OAuth client."""
    oauth = OAuth()

    # Register Google OAuth client
    oauth.register(
        "google",
        client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid email profile",
        },
    )
    return oauth