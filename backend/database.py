import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Allow DATABASE_URL env var for cloud (e.g. Render managed Postgres).
# Falls back to SQLite — uses /tmp on cloud (always writable) or local /data.
_DATABASE_URL = os.environ.get("DATABASE_URL")
if _DATABASE_URL:
    # Render gives postgres:// but SQLAlchemy needs postgresql://
    _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(_DATABASE_URL)
else:
    # Prefer /tmp when running on a read-only cloud filesystem
    _is_cloud = os.environ.get("RENDER") or os.environ.get("RAILWAY_ENVIRONMENT")
    if _is_cloud:
        DB_PATH = Path("/tmp/analytics.db")
    else:
        DB_PATH = Path(__file__).resolve().parent.parent / "data" / "analytics.db"
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from backend import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
