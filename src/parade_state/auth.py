"""Google OAuth authentication utilities."""

from functools import lru_cache

from authlib.integrations.starlette_client import OAuth

from parade_state.utils import env


@lru_cache
def get_oauth() -> OAuth:
    """Get configured OAuth client."""
    oauth = OAuth()

    # Register Google OAuth client
    oauth.register(
        "google",
        client_id=env.get("GOOGLE_CLIENT_ID", ""),
        client_secret=env.get("GOOGLE_CLIENT_SECRET", ""),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid email profile",
        },
    )
    return oauth
