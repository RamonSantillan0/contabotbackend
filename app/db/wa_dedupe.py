from sqlalchemy import text
from sqlalchemy.orm import Session

def mark_processed(db: Session, wamid: str) -> bool:
    """
    True  => primera vez (insert OK)
    False => duplicado (ya existía)
    """
    if not wamid:
        return True  # sin wamid no deduplicamos

    try:
        db.execute(
            text("INSERT INTO wa_processed_messages (wamid) VALUES (:w)"),
            {"w": wamid},
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
