"""Google OAuth authentication utilities."""

import os
from functools import lru_cache
from typing import Optional

from authlib.integrations.starlette_client import OAuth
from starlette.config import Config


@lru_cache
def get_oauth() -> OAuth:
    """Get configured OAuth client."""
    config = Config()
    config.set("GOOGLE_CLIENT_ID", os.getenv("GOOGLE_CLIENT_ID"))
    config.set("GOOGLE_CLIENT_SECRET", os.getenv("GOOGLE_CLIENT_SECRET"))

    oauth = OAuth(config)
    oauth.register(
        "google",
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid email profile",
        },
    )
    return oauth