from pathlib import Path
import logging
import time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..schemas import UploadResponse
from ..services.analyzer import fetch_students, persist_students, save_processed_excel, serialize_student
from ..services.elastic import get_elasticsearch_client, sync_students
from ..services.intelligence import ensure_query_index
from ..services.parser import parse_uploaded_file
from ..services.metrics import log_event


router = APIRouter(prefix="/upload", tags=["upload"])
PROCESSED_FILE_PATH = Path(__file__).resolve().parents[1] / "storage" / "processed_results.xlsx"


@router.post("", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db), _user=Depends(get_current_user)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    file_bytes = await file.read()
    start = time.perf_counter()
    try:
        parse_start = time.perf_counter()
        parsed_students, processed_df = parse_uploaded_file(file_bytes, file.filename)
        parse_ms = int((time.perf_counter() - parse_start) * 1000)
        persist_students(db, parsed_students, owner_user_id=_user.id)
        save_processed_excel(processed_df, PROCESSED_FILE_PATH)
        students = fetch_students(db, owner_user_id=_user.id)
        try:
            elastic_client = get_elasticsearch_client()
            sync_students(elastic_client, students)
        except Exception as exc:
            logging.warning("Skipping Elasticsearch sync during upload: %s", exc)
        try:
            ensure_query_index(students, owner_user_id=_user.id)
        except Exception as exc:
            logging.warning("Skipping query index refresh during upload: %s", exc)
        log_event(
            "ingestion",
            {
                "source": "upload",
                "filename": file.filename,
                "dataset_name": "default",
                "rows": int(len(processed_df)),
                "parsed_students": int(len(parsed_students)),
                "parse_ms": parse_ms,
                "duration_ms": int((time.perf_counter() - start) * 1000),
                "success": True,
            },
        )
    except ValueError as exc:
        log_event(
            "ingestion",
            {
                "source": "upload",
                "filename": file.filename,
                "success": False,
                "error": str(exc),
                "duration_ms": int((time.perf_counter() - start) * 1000),
            },
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log_event(
            "ingestion",
            {
                "source": "upload",
                "filename": file.filename,
                "success": False,
                "error": str(exc),
                "duration_ms": int((time.perf_counter() - start) * 1000),
            },
        )
        raise HTTPException(status_code=500, detail=f"Failed to process file: {exc}") from exc

    student_payload = [serialize_student(student) for student in students]
    failed_count = sum(1 for student in student_payload if student["pass_fail"] == "FAIL")
    return {
        "total_students": len(student_payload),
        "failed_count": failed_count,
        "processed_file_url": "/analytics/download/processed",
        "students": student_payload,
    }
