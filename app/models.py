from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

DATABASE_URL = "sqlite:///./store.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Event(Base):
    __tablename__ = "events"

    event_id    = Column(String, primary_key=True)
    store_id    = Column(String, index=True)
    camera_id   = Column(String)
    visitor_id  = Column(String)
    event_type  = Column(String)
    timestamp   = Column(String)
    zone_id     = Column(String, nullable=True)
    dwell_ms    = Column(Integer, default=0)
    is_staff    = Column(Boolean, default=False)
    confidence  = Column(Float, default=1.0)
    queue_depth = Column(Integer, nullable=True)
    session_seq = Column(Integer, default=1)

def init_db():
    Base.metadata.create_all(bind=engine)