from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class AgentRequest(BaseModel):
    message: str
    session_id: str


class AgentResponse(BaseModel):
    intent: str
    reply: str
    missing: List[str] = []
    data: Optional[Dict[str, Any]] = None
