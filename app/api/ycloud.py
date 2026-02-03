import os
import json
from fastapi import APIRouter, Request, Header, HTTPException, Depends

from app.db.repository import get_db
from app.core.ycloud_signature import verify_ycloud_signature
from app.services.wa_gateway import handle_whatsapp

router = APIRouter()

@router.post("/ycloud/inbound")
async def ycloud_inbound(
    request: Request,
    ycloud_signature: str = Header(default="", alias="ycloud-signature"),
    db = Depends(get_db),
):
    raw = await request.body()

    secret = os.getenv("YCLOUD_WEBHOOK_SECRET", "")
    if not verify_ycloud_signature(ycloud_signature, raw, secret):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # n8n te manda: { "body": { ...payload ycloud... }, "endpoint_id": "..." }
    payload = json.loads(raw.decode("utf-8"))
    event = payload.get("body", payload)  # por si mañana decidís mandar el evento directo

    msg = event.get("whatsappInboundMessage", {})
    from_number = msg.get("from", "")
    to_number = msg.get("to", "")
    message_id = msg.get("wamid")

    # Texto (solo tipo text por ahora)
    text = ""
    if msg.get("type") == "text":
        text = (msg.get("text") or {}).get("body", "") or ""

    reply = handle_whatsapp(db, from_number, text, message_id)

    # para que n8n pueda enviar
    return {"reply": reply, "from": to_number, "to": from_number}
