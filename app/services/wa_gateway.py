import os
import re
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta
from sqlalchemy import text

from app.core.session import SESSION_CTX
from app.schemas.agent import AgentRequest
from app.services.agent_logic import handle_agent

OTP_RE = re.compile(r"^\s*(\d{6})\s*$")

def _now():
    return datetime.utcnow()

def _digits_only(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())

def _hash_otp(code: str) -> str:
    salt = os.getenv("OTP_SALT", "dev_salt")
    msg = (salt + ":" + code).encode("utf-8")
    return hashlib.sha256(msg).hexdigest()

def _reverify_days() -> int:
    try:
        return int(os.getenv("OTP_REVERIFY_DAYS", "30"))
    except:
        return 30

def _otp_ttl_minutes() -> int:
    try:
        return int(os.getenv("OTP_TTL_MINUTES", "10"))
    except:
        return 10

def _dedupe_message(db, message_id: str, wa_id: str) -> bool:
    """Retorna True si es nuevo; False si ya existía."""
    if not message_id:
        return True
    try:
        db.execute(
            text("INSERT INTO whatsapp_message_log(message_id, wa_id) VALUES (:mid, :wa)"),
            {"mid": message_id, "wa": wa_id},
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False

def _find_user_by_phone(db, wa_id: str):
    """
    Busca en usuarios_clientes.telefono. Para MVP hacemos varias variantes:
    - exacto con wa_id digits
    - con '+' delante
    - sin '+'
    """
    digits = wa_id
    variants = {digits, "+" + digits}
    # también intentar sin 549... si te mandan con 54, etc (opcional)
    # variants.add(digits.lstrip("54"))  # si querés

    for v in variants:
        row = db.execute(
            text("""
                SELECT id_usuario, id_cliente, rol, activo
                FROM usuarios_cliente
                WHERE telefono = :tel
                LIMIT 2
            """),
            {"tel": v},
        ).mappings().all()
        if len(row) == 1:
            r = row[0]
            if int(r.get("activo", 1)) != 1:
                return None
            return {"id_usuario": r["id_usuario"], "id_cliente": r["id_cliente"], "rol": r.get("rol")}
        elif len(row) > 1:
            # raro pero posible: mismo tel en varios registros
            return {"ambiguous": True}
    return None

def _get_identity(db, wa_id: str):
    row = db.execute(
        text("""
            SELECT wa_id, id_usuario, id_cliente, verified_at, status
            FROM whatsapp_identity
            WHERE wa_id = :wa
            LIMIT 1
        """),
        {"wa": wa_id},
    ).mappings().first()
    return dict(row) if row else None

def _upsert_identity(db, wa_id: str, id_usuario: int, id_cliente: int):
    db.execute(
        text("""
            INSERT INTO whatsapp_identity(wa_id, id_usuario, id_cliente, last_seen_at)
            VALUES (:wa, :iu, :ic, :now)
            ON DUPLICATE KEY UPDATE
              id_usuario = VALUES(id_usuario),
              id_cliente = VALUES(id_cliente),
              last_seen_at = VALUES(last_seen_at),
              status = 'active'
        """),
        {"wa": wa_id, "iu": id_usuario, "ic": id_cliente, "now": _now()},
    )
    db.commit()

def _mark_verified(db, wa_id: str):
    db.execute(
        text("""
            UPDATE whatsapp_identity
            SET verified_at = :now, last_seen_at = :now, status='active'
            WHERE wa_id = :wa
        """),
        {"wa": wa_id, "now": _now()},
    )
    db.commit()

def _needs_reverify(identity: dict) -> bool:
    if not identity or not identity.get("verified_at"):
        return True
    try:
        verified = identity["verified_at"]
        if isinstance(verified, str):
            return True
        return verified < (_now() - timedelta(days=_reverify_days()))
    except:
        return True

def _create_otp(db, wa_id: str) -> str:
    code = f"{secrets.randbelow(1000000):06d}"
    code_hash = _hash_otp(code)
    exp = _now() + timedelta(minutes=_otp_ttl_minutes())

    db.execute(
        text("""
            INSERT INTO whatsapp_otp(wa_id, code_hash, expires_at)
            VALUES (:wa, :h, :exp)
        """),
        {"wa": wa_id, "h": code_hash, "exp": exp},
    )
    db.commit()
    return code

def _verify_otp(db, wa_id: str, code: str) -> bool:
    row = db.execute(
        text("""
            SELECT id, code_hash, expires_at, attempts, used_at
            FROM whatsapp_otp
            WHERE wa_id = :wa
            ORDER BY id DESC
            LIMIT 1
        """),
        {"wa": wa_id},
    ).mappings().first()

    if not row:
        return False
    if row["used_at"] is not None:
        return False
    if row["attempts"] >= 5:
        return False
    if row["expires_at"] < _now():
        return False

    ok = hmac.compare_digest(row["code_hash"], _hash_otp(code))

    db.execute(
        text("UPDATE whatsapp_otp SET attempts = attempts + 1 WHERE id = :id"),
        {"id": row["id"]},
    )
    if ok:
        db.execute(
            text("UPDATE whatsapp_otp SET used_at = :now WHERE id = :id"),
            {"id": row["id"], "now": _now()},
        )
    db.commit()
    return ok

def handle_whatsapp(db, from_number: str, text_msg: str, message_id: str | None) -> str:
    wa_id = _digits_only(from_number)

    # 0) dedupe
    if message_id and not _dedupe_message(db, message_id, wa_id):
        return "✅ Recibido."  # ya lo procesamos antes

    # 1) whitelist
    user = _find_user_by_phone(db, wa_id)
    if not user:
        return "❌ Tu número no está autorizado para este servicio. Contactá al administrador."
    if user.get("ambiguous"):
        return "⚠️ Tu número aparece en más de un cliente. Contactá al administrador para corregirlo."

    # 2) upsert identidad
    _upsert_identity(db, wa_id, user["id_usuario"], user["id_cliente"])
    identity = _get_identity(db, wa_id)

    # 3) OTP: si falta verificación o caducó
    m = OTP_RE.match(text_msg or "")
    if _needs_reverify(identity):
        # si mandó código, validamos
        if m:
            code = m.group(1)
            if _verify_otp(db, wa_id, code):
                _mark_verified(db, wa_id)
                return "✅ Código verificado. Ahora podés hacer tu consulta (ej: *ventas 2025-12*)."
            else:
                return "❌ Código incorrecto o vencido. Pedí uno nuevo escribiendo: OTP"
        else:
            # si escribe "otp" o cualquier cosa y no está verificado, enviamos otp
            if (text_msg or "").strip().lower() != "otp":
                # para no cortar UX, igualmente mandamos OTP de una
                pass
            code = _create_otp(db, wa_id)
            return f"🔐 Para continuar, te envié un código de verificación: *{code}*.\nRespondé con ese código (6 dígitos)."

    # 4) ya autenticado: fijamos contexto del agente por sesión WA
    session_id = f"wa:{wa_id}"
    ctx = SESSION_CTX.get(session_id)
    if not isinstance(ctx, dict):
        ctx = {}
        SESSION_CTX[session_id] = ctx
    ctx["id_cliente"] = int(user["id_cliente"])

    # 5) pasar al agente real
    agent_resp = handle_agent(db, AgentRequest(message=text_msg, session_id=session_id))
    return agent_resp.reply
