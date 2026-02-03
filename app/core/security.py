import os
from fastapi import Header, HTTPException

def require_internal_api_key(x_api_key: str = Header(default="")):
    expected = os.getenv("INTERNAL_API_KEY", "")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
