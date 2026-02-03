from fastapi import APIRouter, Depends
from app.db.repository import get_db
from app.core.security import require_internal_api_key
from app.schemas.whatsapp import WhatsAppAgentRequest, WhatsAppAgentResponse
from app.services.wa_gateway import handle_whatsapp

router = APIRouter()

@router.post("/wa/agent", response_model=WhatsAppAgentResponse, dependencies=[Depends(require_internal_api_key)])
def wa_agent(req: WhatsAppAgentRequest, db=Depends(get_db)):
    reply = handle_whatsapp(db, req.from_number, req.text, req.message_id)
    return WhatsAppAgentResponse(reply=reply)
