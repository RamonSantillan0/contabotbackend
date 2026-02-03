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
    x_api_key: str = Header(default="", alias="x-api-key"),
    db = Depends(get_db),
):
    raw = await request.body()

    # Parse del wrapper de n8n (si viene)
    payload = None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        payload = None

    # Determinar el raw real a verificar (ideal: raw original firmado por YCloud)
    raw_to_verify = raw
    if isinstance(payload, dict) and "raw_ycloud" in payload and isinstance(payload["raw_ycloud"], str):
        raw_to_verify = payload["raw_ycloud"].encode("utf-8")

    # Flags de verificación
    verify_enabled = os.getenv("YCLOUD_VERIFY_SIGNATURE", "1") == "1"
    internal_key = os.getenv("INTERNAL_API_KEY", "")

    # Si estás en DEV, podés permitir bypass solo desde n8n (con X-API-Key)
    if verify_enabled:
        secret = os.getenv("YCLOUD_WEBHOOK_SECRET", "")
        if not secret:
            raise HTTPException(status_code=500, detail="YCLOUD_WEBHOOK_SECRET not set")

        if not verify_ycloud_signature(ycloud_signature, raw_to_verify, secret):
            raise HTTPException(status_code=401, detail="Invalid signature")
    else:
        # bypass seguro: solo si coincide tu api key interna (evita que cualquiera saltee firma)
        if internal_key and x_api_key != internal_key:
            raise HTTPException(status_code=401, detail="Invalid internal key")

    # Tomar evento: directo o dentro del wrapper
    if isinstance(payload, dict):
        event = payload.get("body", payload)
    else:
        # si no es JSON válido
        raise HTTPException(status_code=400, detail="Invalid JSON")

    msg = event.get("whatsappInboundMessage", {})
    if not msg:
        raise HTTPException(status_code=400, detail="Missing whatsappInboundMessage")

    from_number = msg.get("from", "")
    to_number = msg.get("to", "")
    message_id = msg.get("wamid")

    text = ""
    if msg.get("type") == "text":
        text = (msg.get("text") or {}).get("body", "") or ""

    reply = handle_whatsapp(db, from_number, text, message_id)
    return {"reply": reply, "from": to_number, "to": from_number}
