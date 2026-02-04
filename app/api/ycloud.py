import os
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Header, HTTPException, Depends

from app.db.repository import get_db
from app.core.ycloud_signature import verify_ycloud_signature
from app.services.wa_gateway import handle_whatsapp
from app.db.wa_dedupe import mark_processed  # ✅ NUEVO

router = APIRouter()

@router.post("/ycloud/inbound")
async def ycloud_inbound(
    request: Request,
    ycloud_signature: str = Header(default="", alias="ycloud-signature"),
    x_api_key: str = Header(default="", alias="x-api-key"),
    db = Depends(get_db),
):
    raw = await request.body()

    # 1) Intentar parsear JSON
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 2) Flags
    verify_enabled = os.getenv("YCLOUD_VERIFY_SIGNATURE", "1") == "1"
    internal_key = os.getenv("INTERNAL_API_KEY", "")

    if verify_enabled:
        # Firma real (modo PROD)
        secret = os.getenv("YCLOUD_WEBHOOK_SECRET", "")
        if not secret:
            raise HTTPException(status_code=500, detail="YCLOUD_WEBHOOK_SECRET not set")

        # Si n8n manda raw_ycloud, úsalo; si no, usa raw
        raw_to_verify = raw
        if isinstance(payload, dict) and isinstance(payload.get("raw_ycloud"), str) and payload["raw_ycloud"]:
            raw_to_verify = payload["raw_ycloud"].encode("utf-8")

        if not verify_ycloud_signature(ycloud_signature, raw_to_verify, secret):
            raise HTTPException(status_code=401, detail="Invalid signature")
    else:
        # ✅ Camino 1: BYPASS seguro (solo n8n con API key interna)
        if not internal_key:
            raise HTTPException(status_code=500, detail="INTERNAL_API_KEY not set")
        if x_api_key != internal_key:
            raise HTTPException(status_code=401, detail="Invalid internal key")

    # 3) Tomar evento (wrapper o directo)
    event = payload.get("body", payload) if isinstance(payload, dict) else payload

    msg = event.get("whatsappInboundMessage", {})
    if not msg:
        raise HTTPException(status_code=400, detail="Missing whatsappInboundMessage")

    from_number = msg.get("from", "")
    to_number = msg.get("to", "")
    message_id = msg.get("wamid")  # ✅ clave para dedupe

    # 4) (Opcional) Ignorar mensajes viejos (ej: más de 2 min)
    max_age_seconds = int(os.getenv("YCLOUD_MAX_MSG_AGE_SECONDS", "120"))
    send_time = msg.get("sendTime")
    if send_time:
        try:
            dt = datetime.fromisoformat(send_time.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            if age > max_age_seconds:
                # devolvemos 200 para cortar reintentos
                return {"reply": "", "from": to_number, "to": from_number, "ignored": True, "reason": "old"}
        except Exception:
            pass

    # 5) ✅ DEDUPE: si ya procesamos este wamid, no respondemos otra vez
    if message_id and not mark_processed(db, message_id):
        return {"reply": "", "from": to_number, "to": from_number, "ignored": True, "reason": "duplicate"}

    # 6) Texto (solo tipo text por ahora)
    text = ""
    if msg.get("type") == "text":
        text = (msg.get("text") or {}).get("body", "") or ""

    reply = handle_whatsapp(db, from_number, text, message_id)
    return {"reply": reply, "from": to_number, "to": from_number}
