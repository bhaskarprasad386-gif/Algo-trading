from sqlalchemy import inspect, text

from app.core.database import Base, engine

# Import models so SQLAlchemy registers all tables before create_all.
from app import models  # noqa: F401,E402


def init_db() -> None:
    """Create missing tables and apply safe schema additions without dropping data."""
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully")
