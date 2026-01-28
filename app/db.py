from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from .settings import settings

DB_URL = (
    f"mysql+pymysql://{settings.DB_USER}:{settings.DB_PASSWORD}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    f"?charset=utf8mb4"
)

engine = create_engine(
    DB_URL,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 3},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def test_db_connection() -> dict:
    with engine.connect() as conn:
        r = conn.execute(text("SELECT 1 AS ok")).mappings().one()
        return {"ok": int(r["ok"])}
