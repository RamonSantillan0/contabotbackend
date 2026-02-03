import hmac
import hashlib
import time

def verify_ycloud_signature(signature_header: str, raw_body: bytes, secret: str, window_sec: int = 300) -> bool:
    if not signature_header or not secret:
        return False

    parts = {}
    for kv in signature_header.split(","):
        kv = kv.strip()
        if "=" in kv:
            k, v = kv.split("=", 1)
            parts[k.strip()] = v.strip()

    t = parts.get("t")
    s = parts.get("s")
    if not t or not s:
        return False

    # anti-replay
    try:
        t_int = int(t)
    except ValueError:
        return False

    now = int(time.time())
    if abs(now - t_int) > window_sec:
        return False

    # payload = b"{t}." + raw_body + b"."
    payload = t.encode("utf-8") + b"." + raw_body + b"."
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    return hmac.compare_digest(expected, s)
