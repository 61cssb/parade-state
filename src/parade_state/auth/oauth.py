"""OAuth client configuration.

This module provides OAuth client setup and configuration, specifically
for Google OAuth 2.0 integration.

## Key Functions

- `get_oauth()` - Get configured OAuth client (cached)

## Usage

```python
from parade_state.auth.oauth import get_oauth

oauth = get_oauth()
google = oauth.create_client("google")
return await google.authorize_redirect(request, redirect_uri)
```

## Architecture

This module is part of the core authentication logic and should be imported by:
- `parade_state.web.auth` - OAuth flow endpoints
- Any module that needs OAuth client access

## Configuration

Required environment variables:
- `GOOGLE_CLIENT_ID` - Google OAuth client ID
- `GOOGLE_CLIENT_SECRET` - Google OAuth client secret

Optional environment variables:
- `OAUTH_REDIRECT_URI` - OAuth callback URL (default: http://localhost:8000/auth/callback)

## Google OAuth Setup

1. Create project in Google Cloud Console
2. Enable Google+ API
3. Create OAuth 2.0 credentials
4. Set authorized redirect URI
5. Copy client ID and secret to environment variables
"""

from functools import lru_cache

from authlib.integrations.starlette_client import OAuth

from parade_state.utils import env


@lru_cache
def get_oauth() -> OAuth:
    """Get configured OAuth client.

    Creates and configures an OAuth client with Google registration.
    Uses lru_cache decorator to return the same instance on subsequent calls.

    Returns:
        Configured OAuth client with Google registration

    Example:
        ```python
        oauth = get_oauth()
        google = oauth.create_client("google")

        # Redirect to Google OAuth
        return await google.authorize_redirect(request, redirect_uri)

        # Handle callback
        token = await google.authorize_access_token(request)
        user_info = await google.parse_id_token(request, token)
        ```

    Note:
        This function is cached using lru_cache to ensure only one OAuth
        client instance is created per application lifecycle.
    """
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
