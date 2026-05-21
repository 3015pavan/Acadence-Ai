import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import logging
from sqlalchemy import select

try:
    from . import agent_models
    from . import models
    from .database import Base, SessionLocal, engine
    from .services import query_engine
    from .agents.email_agent import email_agent
    from .auth import get_or_create_default_admin
    from .routes.auth import router as auth_router
    from .routes.agent import router as agent_router
    from .routes.analytics import router as analytics_router
    from .routes.upload import router as upload_router
    from .services.analyzer import fetch_students
    from .services.intelligence import ensure_query_index
except ImportError:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from backend import agent_models
    from backend import models
    from backend.database import Base, SessionLocal, engine
    from backend.services import query_engine
    from backend.agents.email_agent import email_agent
    from backend.auth import get_or_create_default_admin
    from backend.routes.auth import router as auth_router
    from backend.routes.agent import router as agent_router
    from backend.routes.analytics import router as analytics_router
    from backend.routes.upload import router as upload_router
    from backend.services.analyzer import fetch_students
    from backend.services.intelligence import ensure_query_index


Base.metadata.create_all(bind=engine)

app = FastAPI(title="Student Result Analytics")

# Configure basic structured logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("backend")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to specific origins
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["content-length", "content-type"],
    max_age=3600,  # Cache preflight requests for 1 hour
)

app.include_router(upload_router)
app.include_router(analytics_router)
app.include_router(agent_router)
app.include_router(auth_router)
app.include_router(upload_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")
app.include_router(agent_router, prefix="/api")
app.include_router(auth_router, prefix="/api")


@app.on_event("startup")
def warm_query_index():
    db = SessionLocal()
    try:
        get_or_create_default_admin(db)
        users = list(db.scalars(select(models.User)).all())
        for user in users:
            students = fetch_students(db, owner_user_id=user.id)
            if not students:
                continue
            try:
                ensure_query_index(students, owner_user_id=user.id)
            except Exception as e:
                # Fail safe: don't block app startup if index rebuild fails
                print(f"Warning: ensure_query_index failed on startup for user {user.id}:", e)
    finally:
        db.close()

    if os.getenv("AGENT_AUTO_START", "false").strip().lower() == "true":
        try:
            email_agent.start()
        except Exception as exc:
            print("Warning: email agent auto-start failed:", exc)


@app.on_event("shutdown")
def shutdown_background_agent():
    email_agent.shutdown()


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/metrics")
def metrics():
    # Expose simple internal metrics for debugging and monitoring
    try:
        return {"metrics": query_engine._QUERY_METRICS}
    except Exception:
        return {"metrics": {}}


@app.get("/api/health", include_in_schema=False)
def api_health_check():
    return {"status": "ok"}
