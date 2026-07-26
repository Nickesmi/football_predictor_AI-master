"""
Shared FastAPI dependencies module.
Provides common request validation, authentication, and database connection dependencies.
"""

from typing import Optional
from fastapi import Header, HTTPException
from src.config import ADMIN_API_KEY


def verify_admin_key(x_api_key: Optional[str] = Header(None)) -> Optional[str]:
    """
    Verify the admin API key provided in request headers.

    Args:
        x_api_key: The X-API-Key header value.

    Returns:
        The validated API key string if valid.

    Raises:
        HTTPException: If the provided key is missing or does not match ADMIN_API_KEY.
    """
    if not x_api_key or x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing admin API key")
    return x_api_key
