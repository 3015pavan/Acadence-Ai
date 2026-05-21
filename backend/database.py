import os
import re

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker


load_dotenv()


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    return database_url


DATABASE_URL = _normalize_database_url(
    os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@localhost:5432/acadextract",
    )
)
DB_SCHEMA = os.getenv("DB_SCHEMA", "student_app")


def _validate_schema_name(name: str) -> str:
    # Allow only alphanumerics and underscores for schema names to avoid injection
    safe = re.sub(r"[^a-zA-Z0-9_]", "", str(name))
    if not safe:
        raise RuntimeError("Invalid DB_SCHEMA environment variable; must contain alphanumeric or underscore characters.")
    return safe


DB_SCHEMA = _validate_schema_name(DB_SCHEMA)

engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
    connect_args={"options": f"-csearch_path={DB_SCHEMA}"},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


with engine.begin() as connection:
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}" AUTHORIZATION CURRENT_USER'))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
