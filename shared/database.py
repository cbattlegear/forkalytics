"""
Shared database connection and utilities
"""
import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager

from .models import Base, Instance

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://forkalytics:forkalytics_secret@localhost:5432/forkalytics")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all database tables"""
    Base.metadata.create_all(bind=engine)
    
    # Ensure default instance exists
    ensure_default_instance()


def ensure_default_instance():
    """Ensure default instance exists, create if not"""
    from datetime import datetime
    
    mastodon_instance = os.getenv("MASTODON_INSTANCE", "https://mastodon.social")
    stream_type = os.getenv("STREAM_TYPE", "public:local")
    
    try:
        with get_db_session() as db:
            instance = db.query(Instance).filter(Instance.base_url == mastodon_instance).first()
            
            if not instance:
                instance = Instance(
                    base_url=mastodon_instance,
                    stream_type=stream_type,
                    created_at=datetime.utcnow(),
                    last_seen_at=datetime.utcnow()
                )
                db.add(instance)
                db.flush()
                logger.info(f"Created default instance: {mastodon_instance} (ID: {instance.id})")
            
            return instance.id
    except Exception as e:
        logger.warning(f"Could not ensure default instance: {e}")
        return 1  # Return default ID


def get_default_instance_id():
    """Get the default instance ID"""
    mastodon_instance = os.getenv("MASTODON_INSTANCE", "https://mastodon.social")
    
    try:
        with get_db_session() as db:
            instance = db.query(Instance).filter(Instance.base_url == mastodon_instance).first()
            if instance:
                return instance.id
    except Exception as e:
        logger.warning(f"Could not get default instance ID: {e}")
    
    # Fallback to ID 1
    return 1


def get_db() -> Session:
    """Get database session for FastAPI dependency injection"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_session():
    """Context manager for database sessions"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
