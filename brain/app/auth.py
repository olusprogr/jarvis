from fastapi import Header, HTTPException
from . import config


def require_api_key(x_jarvis_key: str = Header(default="")) -> None:
    if not config.JARVIS_API_KEY:
        raise HTTPException(500, "JARVIS_API_KEY not configured on server")
    if x_jarvis_key != config.JARVIS_API_KEY:
        raise HTTPException(401, "Invalid or missing X-Jarvis-Key header")
