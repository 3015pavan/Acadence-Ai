from fastapi import APIRouter, Depends, HTTPException, Query
import time
import uuid
from sqlalchemy import select
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_role, optional_current_user
from ..database import get_db
from ..schemas import DatasetDeleteResponse, QueryRequest, QueryResponse, StudentTableResponse, SummaryResponse
from ..services.analyzer import build_summary, delete_dataset, fetch_students, serialize_student
from ..services.query_engine import execute_query
from ..services.reporting import generate_report_pdf
from ..services.intelligence import INDEX_FILE, METADATA_FILE, ensure_query_index
from .upload import PROCESSED_FILE_PATH
from ..services.metrics import log_event
from ..services.elastic import get_elasticsearch_client, sync_students


router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=SummaryResponse)
def get_summary(dataset_ids: str | None = Query(None, description="Comma-separated dataset ids to filter"), db: Session = Depends(get_db), _user=Depends(get_current_user)):
    try:
        ids = []
        if dataset_ids:
            try:
                ids = [int(s) for s in dataset_ids.split(",") if s.strip()]
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid dataset_ids parameter; must be comma-separated integers.")
        summary = build_summary(db, dataset_ids=ids if ids else None, owner_user_id=_user.id)
        topper = serialize_student(summary["topper"]) if summary["topper"] else None
        return {
            "topper": topper,
            "average_sgpa": summary["average_sgpa"],
            "total_students": summary["total_students"],
            "failed_count": summary["failed_count"],
        }
    except HTTPException:
        raise
    except Exception:
        # Fail safe for development: return empty/default summary instead of 500
        return {"topper": None, "average_sgpa": 0.0, "total_students": 0, "failed_count": 0}


@router.get("/students", response_model=StudentTableResponse)
def get_students(
    dataset_ids: str | None = Query(None, description="Comma-separated dataset ids to filter"),
    merge: str | None = Query("union", description="Merge policy: 'union' or 'prefer_latest'"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    try:
        ids = []
        if dataset_ids:
            try:
                ids = [int(s) for s in dataset_ids.split(",") if s.strip()]
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid dataset_ids parameter; must be comma-separated integers.")
        students = fetch_students(db, dataset_ids=ids if ids else None, merge=merge, owner_user_id=_user.id)
        return {"students": [serialize_student(student) for student in students]}
    except HTTPException:
        raise
    except Exception:
        # Fail safe: return empty list so frontend can render without backend errors
        return {"students": []}


@router.get("/query")
def query_students_help():
    return {
        "message": "Use POST /analytics/query with a JSON body like {'query': 'average SGPA'}.",
        "examples": [
            "average SGPA",
            "topper",
            "who failed",
            "top 5 students",
        ],
    }


def _run_query(payload: QueryRequest, db: Session, user_id: int | None) -> QueryResponse | dict:
    query_id = str(uuid.uuid4())
    start = time.perf_counter()
    try:
        history = [
            {"role": message.role, "content": message.content, "student_usns": message.student_usns}
            for message in payload.history
        ]
        # Support optional file/dataset filtering via payload.file_ids
        file_ids = getattr(payload, "file_ids", []) or []
        merge = getattr(payload, "merge", "union") or "union"
        response = execute_query(db, payload.query, history=history, dataset_ids=file_ids, merge=merge, owner_user_id=user_id)
        duration_ms = int((time.perf_counter() - start) * 1000)
        meta = response.get("meta", {}) if isinstance(response, dict) else {}
        planner = meta.get("planner", {}) if isinstance(meta, dict) else {}
        log_event(
            "query",
            {
                "query_id": query_id,
                "query": payload.query,
                "intent": response.get("intent") if isinstance(response, dict) else None,
                "query_type": meta.get("query_type"),
                "intent_source": planner.get("intent_source"),
                "mode": planner.get("mode"),
                "classification": planner.get("classification"),
                "confidence": meta.get("confidence"),
                "citations": len(meta.get("citations", []) or []),
                "students": len(response.get("students", []) or []) if isinstance(response, dict) else 0,
                "retrieved_chunks": meta.get("retrieved_chunks"),
                "matching_results": meta.get("matching_results"),
                "context_ms": meta.get("context_ms"),
                "generation_ms": meta.get("generation_ms"),
                "duration_ms": duration_ms,
                "dataset_ids": file_ids,
                "merge": merge,
                "status": "ok",
            },
        )
        return response
    except FileNotFoundError:
        duration_ms = int((time.perf_counter() - start) * 1000)
        log_event(
            "query",
            {
                "query_id": query_id,
                "query": payload.query,
                "duration_ms": duration_ms,
                "dataset_ids": getattr(payload, "file_ids", []) or [],
                "merge": getattr(payload, "merge", "union") or "union",
                "status": "error",
                "error": "query_index_not_ready",
            },
        )
        raise HTTPException(status_code=400, detail="Query index is not ready yet. Upload data first.") from None
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        log_event(
            "query",
            {
                "query_id": query_id,
                "query": payload.query,
                "duration_ms": duration_ms,
                "dataset_ids": getattr(payload, "file_ids", []) or [],
                "merge": getattr(payload, "merge", "union") or "union",
                "status": "error",
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail=f"Failed to execute query: {exc}") from exc


@router.post("/query", response_model=QueryResponse)
def query_students(payload: QueryRequest, db: Session = Depends(get_db)):
    return _run_query(payload, db, None)


@router.get("/datasets")
def list_datasets(db: Session = Depends(get_db), _user=Depends(get_current_user)):
    try:
        from .. import models

        stmt = select(models.Dataset).where(models.Dataset.owner_user_id == _user.id).order_by(models.Dataset.name)
        datasets = list(db.scalars(stmt).all())
        return [{"id": d.id, "name": d.name, "source": getattr(d, "source", "upload")} for d in datasets]
    except Exception:
        return []


@router.delete("/datasets/{dataset_id}", response_model=DatasetDeleteResponse)
def remove_dataset(dataset_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    try:
        result = delete_dataset(db, dataset_id, owner_user_id=_user.id)
        remaining_students = result.get("remaining_students", [])

        try:
            elastic_client = get_elasticsearch_client()
            sync_students(elastic_client, remaining_students)
        except Exception as exc:
            log_event(
                "dataset_delete",
                {
                    "dataset_id": dataset_id,
                    "status": "warning",
                    "warning": f"elasticsearch_sync_failed: {exc}",
                },
            )

        try:
            if remaining_students:
                ensure_query_index(remaining_students, owner_user_id=_user.id)
            else:
                if INDEX_FILE.exists():
                    INDEX_FILE.unlink()
                if METADATA_FILE.exists():
                    METADATA_FILE.unlink()
        except Exception as exc:
            log_event(
                "dataset_delete",
                {
                    "dataset_id": dataset_id,
                    "status": "warning",
                    "warning": f"query_index_refresh_failed: {exc}",
                },
            )

        return {
            **result,
            "remaining_students": len(remaining_students),
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete dataset: {exc}") from exc


@router.post("/query/", response_model=QueryResponse, include_in_schema=False)
def query_students_with_trailing_slash(payload: QueryRequest, db: Session = Depends(get_db)):
    return _run_query(payload, db, None)


@router.post("", response_model=QueryResponse, include_in_schema=False)
def query_students_router_root(payload: QueryRequest, db: Session = Depends(get_db)):
    return _run_query(payload, db, None)


@router.post("/report")
def create_report(db: Session = Depends(get_db), _user=Depends(get_current_user)):
    try:
        pdf_bytes = generate_report_pdf(db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {exc}") from exc

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="student-intelligence-report.pdf"'},
    )


@router.get("/subject-wise")
def get_subject_wise_analysis(
    dataset_ids: str | None = Query(None, description="Comma-separated dataset ids to filter"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Get subject-wise grade distribution and statistics."""
    try:
        from sqlalchemy import func
        from .. import models
        
        ids = []
        if dataset_ids:
            try:
                ids = [int(s) for s in dataset_ids.split(",") if s.strip()]
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid dataset_ids parameter.")
        
        # Query subject-wise statistics
        query = db.query(
            models.Result.subject,
            func.count(models.Result.id).label("total_count"),
            func.avg(models.Result.gp).label("avg_gp"),
        )
        
        if ids:
            query = query.join(models.Student).join(models.Dataset).filter(models.Dataset.id.in_(ids))
        
        subject_stats = query.group_by(models.Result.subject).all()
        
        # Build response
        subjects = []
        for subject, total_count, avg_gp in subject_stats:
            # Count grades for this subject
            grade_query = db.query(
                models.Result.grade,
                func.count(models.Result.id).label("count"),
            ).filter(models.Result.subject == subject)
            
            if ids:
                grade_query = grade_query.join(models.Student).join(models.Dataset).filter(models.Dataset.id.in_(ids))
            
            grade_dist = {row[0]: row[1] for row in grade_query.group_by(models.Result.grade).all()}
            
            subjects.append({
                "subject": subject,
                "total_students": total_count,
                "avg_gp": round(avg_gp, 2) if avg_gp else 0.0,
                "grade_distribution": grade_dist,
                "difficulty": "Hard" if (avg_gp or 0) < 20 else ("Medium" if (avg_gp or 0) < 25 else "Easy"),
            })
        
        # Sort by average GP (weak subjects first)
        subjects.sort(key=lambda x: x["avg_gp"])
        
        return {
            "subjects": subjects,
            "total_subjects": len(subjects),
            "weak_subjects": [s for s in subjects if s["difficulty"] == "Hard"][:5],
            "strong_subjects": [s for s in subjects if s["difficulty"] == "Easy"][:5],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get subject-wise analysis: {exc}") from exc


@router.get("/download/processed")
def download_processed_file():
    if not PROCESSED_FILE_PATH.exists():
        raise HTTPException(status_code=404, detail="No processed file available yet.")
    return FileResponse(
        path=PROCESSED_FILE_PATH,
        filename="processed_results.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/reindex")
def reindex_dataset(db: Session = Depends(get_db), _role: str = Depends(require_role(["admin"]))):
    """Manually rebuild the semantic query index from current students."""
    try:
        students = fetch_students(db, owner_user_id=_user.id)
        if not students:
            raise HTTPException(status_code=400, detail="No students available to index. Upload data first.")
        ensure_query_index(students, owner_user_id=_user.id)
        return {"status": "ok", "indexed_documents": len(students)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to rebuild index: {exc}") from exc
