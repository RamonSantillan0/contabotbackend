from pydantic import BaseModel
from typing import List, Optional, Any, Dict

class AgentRequest(BaseModel):
    message: str
    session_id: str

class AgentResponse(BaseModel):
    intent: str
    reply: str
    missing: List[str] = []
    data: Optional[Dict[str, Any]] = None
