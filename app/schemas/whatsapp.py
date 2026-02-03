from pydantic import BaseModel
from typing import Optional

class WhatsAppAgentRequest(BaseModel):
    from_number: str
    text: str
    message_id: Optional[str] = None

class WhatsAppAgentResponse(BaseModel):
    reply: str
