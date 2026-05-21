import pytest
import json
from sqlalchemy import delete

from backend.database import SessionLocal, engine, Base
from backend import models
from backend.services.analyzer import persist_students, fetch_students
from backend.services.query_engine import execute_query
from backend.services.intelligence import ensure_query_index, retrieve_context_documents
from backend.services.parser import ParsedStudent


@pytest.fixture(scope="module")
def db():
    # Recreate tables from models to ensure schema matches (drop any existing)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def make_parsed(usn: str, name: str, sgpa: float = 8.5):
    return ParsedStudent(
        usn=usn,
        name=name,
        semester=1,
        sgpa=sgpa,
        cgpa=sgpa,
        pass_fail="PASS",
        results=[{"subject": "Math", "grade": "A", "gp": 9.0}],
    )


def test_tenant_isolation_queries_and_semantic_index(db):
    # Create two tenant users
    u1 = models.User(email="tenant1@example.com", password_hash="x", role="teacher", display_name="Tenant1", tenant_key="t1")
    u2 = models.User(email="tenant2@example.com", password_hash="x", role="teacher", display_name="Tenant2", tenant_key="t2")
    db.add_all([u1, u2])
    db.commit()
    db.refresh(u1)
    db.refresh(u2)

    # Persist one student per tenant
    s1 = make_parsed("1MS21CS001", "Alice", sgpa=9.1)
    s2 = make_parsed("1MS21CS002", "Bob", sgpa=7.2)

    persist_students(db, [s1], dataset_name="ds1", owner_user_id=u1.id)
    persist_students(db, [s2], dataset_name="ds2", owner_user_id=u2.id)

    db.commit()

    # Verify fetch_students returns tenant-scoped results
    students_t1 = fetch_students(db, owner_user_id=u1.id)
    students_t2 = fetch_students(db, owner_user_id=u2.id)

    assert any(st.usn == "1MS21CS001" for st in students_t1)
    assert all(st.owner_user_id == u1.id for st in students_t1)
    assert any(st.usn == "1MS21CS002" for st in students_t2)
    assert all(st.owner_user_id == u2.id for st in students_t2)

    # Run structured query per-tenant
    res1 = execute_query(db, "topper", owner_user_id=u1.id)
    res2 = execute_query(db, "topper", owner_user_id=u2.id)

    assert res1.get("students") and any(s.get("usn") == "1MS21CS001" for s in res1.get("students", []))
    assert res2.get("students") and any(s.get("usn") == "1MS21CS002" for s in res2.get("students", []))

    # Build semantic indices per-tenant and verify retrieval isolation
    ensure_query_index(students_t1, owner_user_id=u1.id)
    ensure_query_index(students_t2, owner_user_id=u2.id)

    hits_t1 = retrieve_context_documents("Alice", owner_user_id=u1.id)
    hits_t2 = retrieve_context_documents("Alice", owner_user_id=u2.id)

    assert any("Alice" in h.get("page_content", "") for h in hits_t1)
    assert not any("Alice" in h.get("page_content", "") for h in hits_t2)

    # Cleanup created rows for repeatable test runs
    for tbl in (models.SemanticDocument, models.Result, models.StudentSemester, models.Student, models.Dataset, models.User):
        db.execute(delete(tbl))
    db.commit()
