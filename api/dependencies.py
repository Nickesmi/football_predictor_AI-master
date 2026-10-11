"""
Shared FastAPI dependencies module.
Provides common request validation, authentication, and database connection dependencies.
"""

import hmac
from typing import Optional

from fastapi import Header, HTTPException

from src import config


def verify_admin_key(x_api_key: Optional[str] = Header(None)) -> Optional[str]:
    """
    Verify the admin API key provided in request headers.

    Args:
        x_api_key: The X-API-Key header value.

    Returns:
        The validated API key string if valid.

    Raises:
        HTTPException: 503 if no ADMIN_API_KEY is configured (admin API disabled),
            401 if the provided key is missing or does not match.
    """
    expected = config.ADMIN_API_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="Admin API disabled: ADMIN_API_KEY is not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing admin API key")
    return x_api_key
