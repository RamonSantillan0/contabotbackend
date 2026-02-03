import hmac
import hashlib
import time

def verify_ycloud_signature(signature_header: str, raw_body: bytes, secret: str) -> bool:
    # signature_header: "t=1770099810,s=..."
    if not signature_header or not secret:
        return False

    parts = dict(
        p.split("=", 1) for p in signature_header.split(",") if "=" in p
    )
    t = parts.get("t")
    # anti-replay: 5 minutos
    now = int(time.time())
    if abs(now - int(t)) > 300:
        return False    
    s = parts.get("s")
    if not t or not s:
        return False

    # payload: "{t}.{raw_body}."
    payload_str = f"{t}.{raw_body.decode('utf-8') }."
    expected = hmac.new(
        secret.encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, s)
