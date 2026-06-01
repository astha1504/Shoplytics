from sqlalchemy import Boolean, Column, Integer, String, Text, UniqueConstraint

from backend.database import Base


class StoredEvent(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("event_id", name="uq_event_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(64), nullable=False, index=True)
    visitor_id = Column(String(32), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    timestamp = Column(String(64), nullable=False)
    zone = Column(String(64), nullable=True)
    dwell_ms = Column(Integer, nullable=True)
    queue_depth = Column(Integer, nullable=True)
    store_id = Column(String(32), nullable=True)
    camera = Column(String(32), nullable=True)
    is_staff = Column(Boolean, default=False)
    confidence = Column(String(16), nullable=True)
    raw_json = Column(Text, nullable=True)
