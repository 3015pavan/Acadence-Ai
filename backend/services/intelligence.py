import hashlib
import json
import os
import re
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import requests
from dotenv import load_dotenv
from sqlalchemy import delete, select

from ..database import SessionLocal
from .. import models
from ..models import Student
from ..tenant_context import get_current_user_id

try:
    from langchain_core.documents import Document
except ImportError:
    @dataclass
    class Document:  # type: ignore[override]
        page_content: str
        metadata: Dict[str, object]

load_dotenv()

logger = logging.getLogger(__name__)

EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local").strip().lower()
# Canonical Gemini key source for this project.
GEMINI_API_KEY = os.getenv("GCP_GEMINI_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "low").strip().lower()
GEMINI_TEMPERATURE = os.getenv("GEMINI_TEMPERATURE", "").strip()
INTENT_THRESHOLD = float(os.getenv("AI_INTENT_THRESHOLD", "0.2"))
SEMANTIC_FALLBACK_THRESHOLD = max(INTENT_THRESHOLD, 0.55)
VECTOR_DIMENSION = int(os.getenv("AI_VECTOR_DIMENSION", "384"))
INDEX_DIR = Path(__file__).resolve().parents[1] / "storage" / "semantic"
INDEX_FILE = INDEX_DIR / "query_intelligence.json"
METADATA_FILE = INDEX_DIR / "query_intelligence.metadata.json"
PGVECTOR_AVAILABLE = False
try:  # pragma: no cover - optional dependency
    from pgvector.sqlalchemy import Vector as PGVector  # type: ignore

    PGVECTOR_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    PGVector = None

INTENT_LIBRARY: Dict[str, Dict[str, object]] = {
    "GET_TOPPER": {
        "description": "Return the single highest SGPA student.",
        "examples": [
            "topper",
            "who is the topper",
            "highest sgpa",
            "best student",
            "show the first rank student",
        ],
    },
    "GET_FAILED": {
        "description": "Return students with at least one failing grade.",
        "examples": [
            "who failed",
            "failed students",
            "show students who got f",
            "students with arrears",
            "list failures",
        ],
    },
    "GET_FAILED_IN_SUBJECT": {
        "description": "Return students who failed in a specific subject.",
        "examples": [
            "students who failed in engineering chemistry lab",
            "failed in design thinking",
            "who failed in advanced calculus",
            "list failed students in ma11",
            "students with f in chemistry",
        ],
    },
    "GET_PASSED_IN_SUBJECT": {
        "description": "Return students who passed in a specific subject.",
        "examples": [
            "students who passed in advanced calculus",
            "passed in design thinking",
            "who passed in engineering chemistry lab",
            "list passed students in ma11",
            "students with a in calculus",
        ],
    },
    "GET_RESULT_BY_NAME": {
        "description": "Return detailed result for one student matched by name.",
        "examples": [
            "result of abir",
            "show abir result",
            "marks for ananya",
            "student result by name",
            "give me the result of rahul",
            "what is the sgpa of brinda jagadeesh",
            "what grades did abir goldar receive",
        ],
    },
    "GET_RESULT_BY_USN": {
        "description": "Return detailed result for one student matched by exact USN.",
        "examples": [
            "show 1ms21cs001",
            "student with usn 1ms21cs001",
            "show all details of student with usn 1ms21cs001",
        ],
    },
    "GET_STUDENTS_WITH_GRADE": {
        "description": "Return students who received a requested grade like A+.",
        "examples": [
            "students with a+",
            "who got a plus",
            "list students with grade a+",
            "show a+ students",
            "students scoring grade o",
            "students got grade a+ in design thinking",
            "who received a+ in chemistry",
            "list students with grade o in ma11",
        ],
    },
    "GET_USN_PREFIX": {
        "description": "Return students filtered by a USN prefix.",
        "examples": [
            "students with usn prefix 1ms22",
            "show usn starting with 1ms",
            "usn prefix 4al",
            "list students whose usn begins with 1rv",
            "find usn starts with 2sd",
        ],
    },
    "GET_NAME_PREFIX": {
        "description": "Return students filtered by a name prefix.",
        "examples": [
            "name prefix an",
            "students whose name starts with ra",
            "show names beginning with ab",
            "find students starting with sh",
            "name starts with pooja",
        ],
    },
    "GET_AVERAGE_SGPA": {
        "description": "Return the overall average SGPA.",
        "examples": [
            "average sgpa",
            "mean sgpa",
            "class average",
            "overall sgpa average",
            "what is the average result",
        ],
    },
    "GET_AVERAGE_GP": {
        "description": "Return the overall average grade point across all subjects.",
        "examples": [
            "average gp",
            "what is the average grade point",
            "average grade point of all students",
        ],
    },
    "GET_TOP_N": {
        "description": "Return the top N students ordered by SGPA.",
        "examples": [
            "top 5 students",
            "top five rank holders",
            "best 10 students",
            "show top 3 by sgpa",
            "give me the top students",
        ],
    },
    "GET_SGPA_RANGE": {
        "description": "Return students with SGPA in a specified range.",
        "examples": [
            "students with sgpa greater than 8",
            "sgpa above 8",
            "students with sgpa between 8 and 9",
            "sgpa less than 5",
            "list students with sgpa from 7 to 10",
        ],
    },
    "GET_GRADE_BUT_FAILED": {
        "description": "Return students who have a requested good grade but failed another subject.",
        "examples": [
            "students with a+ but failed in another subject",
            "who has a grade but also failed",
            "students with o grade and also failed",
            "a+ students who still failed",
            "good grade but fail elsewhere",
        ],
    },
    "GET_INCONSISTENT_PERFORMERS": {
        "description": "Return students with wide variation between strong and weak subject performance.",
        "examples": [
            "inconsistent performers",
            "students with mixed performance",
            "who is inconsistent across subjects",
            "students with very uneven grades",
            "show unstable performers",
        ],
    },
    "GET_GP_ZERO_WITH_A": {
        "description": "Return students who have zero grade points in one subject but A or A+ grades in others.",
        "examples": [
            "gp 0 but also a grades",
            "students with gp zero and a+",
            "who has 0 gp and still got a grade",
            "students with zero gp but strong grades",
            "gp equals 0 and a grades",
        ],
    },
    "GET_GP_ZERO_ANY": {
        "description": "Return students who have GP equal to zero in at least one subject.",
        "examples": [
            "students with gp 0",
            "find all students whose gp is 0 in any subject",
            "who has gp zero",
        ],
    },
    "GET_ALL_STUDENTS": {
        "description": "Return all students with their USNs.",
        "examples": [
            "list all students and their usns",
            "show all students",
            "list all student records",
        ],
    },
    "GET_FAILED_COUNT": {
        "description": "Return the number of students who failed in at least one subject.",
        "examples": [
            "how many students failed",
            "count failed students",
            "number of failures",
        ],
    },
    "GET_TOTAL_STUDENTS": {
        "description": "Return the total number of students.",
        "examples": [
            "count total number of students",
            "how many students are there",
            "total students in the file",
        ],
    },
    "GET_MOST_FREQUENT_GRADE": {
        "description": "Return the grade that appears most frequently.",
        "examples": [
            "which grade appears most frequently",
            "most common grade",
            "top grade frequency",
        ],
    },
    "GET_ALL_SUBJECTS": {
        "description": "Return a list of all subjects in the dataset.",
        "examples": [
            "list all subjects",
            "show all subjects",
            "subjects list",
            "what subjects are in the dataset",
        ],
    },
    "GET_ALL_PASSING": {
        "description": "Return students who passed all subjects without any F grade.",
        "examples": [
            "which students have all passing grades",
            "students with no f",
            "all pass students",
        ],
    },
}

SCHEMA_DESCRIPTIONS = [
    "students table stores usn, name, and sgpa for each student record",
    "results table stores per subject grade and grade points linked to a student",
    "pass fail status is derived from result grades where F means fail",
    "elasticsearch is used for search and prefix filtering by name usn and grades",
]

NON_NAME_QUERY_TERMS = {
    "about",
    "all",
    "analysis",
    "analytics",
    "average",
    "best",
    "class",
    "cohort",
    "compare",
    "comparison",
    "dataset",
    "details",
    "explain",
    "failed",
    "failure",
    "give",
    "grades",
    "how",
    "insight",
    "insights",
    "list",
    "mean",
    "most",
    "overview",
    "performance",
    "rank",
    "result",
    "results",
    "show",
    "student",
    "students",
    "subject",
    "subjects",
    "summarize",
    "summary",
    "tell",
    "top",
    "topper",
    "trend",
    "trends",
    "what",
    "who",
    "why",
}


def _extract_json_object(text: str) -> Optional[Dict[str, object]]:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _post_without_env_proxy(url: str, **kwargs):
    # Ignore broken machine-level proxy env vars so local/OEM LLM calls can still work.
    with requests.Session() as session:
        session.trust_env = False
        return session.post(url, **kwargs)


def _tokenize(text: str) -> List[str]:
    lowered = text.lower()
    words = re.findall(r"[a-z0-9\+]+", lowered)
    shingles: List[str] = []
    compact = re.sub(r"\s+", " ", lowered).strip()
    if compact:
        for index in range(max(0, len(compact) - 2)):
            shingles.append(f"3g:{compact[index:index + 3]}")
    return words + shingles


def _hash_to_bucket(token: str, dimension: int) -> int:
    digest = hashlib.md5(token.encode("utf-8")).hexdigest()
    return int(digest, 16) % dimension


def _local_embed_text(text: str) -> np.ndarray:
    vector = np.zeros(VECTOR_DIMENSION, dtype="float32")
    for token in _tokenize(text):
        vector[_hash_to_bucket(token, VECTOR_DIMENSION)] += 1.0
    norm = np.linalg.norm(vector)
    if norm > 0:
        vector /= norm
    return vector


def _gemini_chat_json(
    system_prompt: str,
    user_prompt: str,
    *,
    history: Optional[Sequence[Dict[str, object]]] = None,
    timeout_seconds: int = 12,
) -> Optional[Dict[str, object]]:
    if not GEMINI_API_KEY or not GEMINI_MODEL:
        return None

    try:
        message_history = [{"role": "system", "content": system_prompt}]
        for item in history or []:
            role = str(item.get("role", "user"))
            content = str(item.get("content", "")).strip()
            if content and role in {"system", "user", "assistant"}:
                message_history.append({"role": role, "content": content})
        message_history.append({"role": "user", "content": user_prompt})

        # Gemini generateContent format: single system instruction + conversational contents.
        system_text = "\n\n".join(
            str(item.get("content", "")).strip()
            for item in message_history
            if str(item.get("role", "")) == "system" and str(item.get("content", "")).strip()
        )
        contents = []
        for item in message_history:
            role = str(item.get("role", "user"))
            content = str(item.get("content", "")).strip()
            if not content or role == "system":
                continue
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": content}]})

        payload: Dict[str, object] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": 512,
            },
        }
        # Only add thinkingConfig for models that support it (e.g., gemini-2.0-pro, not gemini-2.5-flash)
        if GEMINI_THINKING_LEVEL and "2.0" in GEMINI_MODEL:
            payload["generationConfig"]["thinkingConfig"] = {"thinkingLevel": GEMINI_THINKING_LEVEL}
        if GEMINI_TEMPERATURE:
            try:
                payload["generationConfig"]["temperature"] = float(GEMINI_TEMPERATURE)
            except ValueError:
                logger.warning("Ignoring invalid GEMINI_TEMPERATURE=%s", GEMINI_TEMPERATURE)
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        response = _post_without_env_proxy(
            f"{GEMINI_BASE_URL}/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        text = ""
        try:
            if isinstance(data, dict) and data.get("candidates"):
                candidate = data.get("candidates")[0]
                content = candidate.get("content", {}) if isinstance(candidate, dict) else {}
                for part in content.get("parts", []) if isinstance(content, dict) else []:
                    if part.get("text"):
                        text += part.get("text", "")
        except Exception:
            text = ""
        parsed = _extract_json_object(str(text))
        if isinstance(parsed, dict):
            return parsed
        return {"answer": str(text).strip()}
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status:
            logger.warning("LLM chat request failed with HTTP status %s", status)
        else:
            logger.warning("LLM chat request failed: %s", exc.__class__.__name__)
        return None


def _llm_chat_json(
    system_prompt: str,
    user_prompt: str,
    *,
    history: Optional[Sequence[Dict[str, object]]] = None,
    timeout_seconds: int = 12,
) -> Optional[Dict[str, object]]:
    return _gemini_chat_json(system_prompt, user_prompt, history=history, timeout_seconds=timeout_seconds)


def embed_texts(texts: Sequence[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, VECTOR_DIMENSION), dtype="float32")

    if EMBEDDING_PROVIDER != "local":
        logger.warning("Unsupported EMBEDDING_PROVIDER=%s; using local embeddings.", EMBEDDING_PROVIDER)
    return np.vstack([_local_embed_text(text) for text in texts]).astype("float32")


def _intent_documents() -> List[Document]:
    documents: List[Document] = []
    for intent, config in INTENT_LIBRARY.items():
        description = str(config["description"])
        for example in config["examples"]:
            documents.append(
                Document(
                    page_content=f"{example}. Intent: {intent}. {description}",
                    metadata={"type": "intent", "intent": intent, "example": example},
                )
            )
    return documents


def _student_documents(students: Iterable[Student]) -> List[Document]:
    documents: List[Document] = []
    for student in students:
        sorted_results = sorted(student.results, key=lambda item: item.subject.lower())
        result_summary = "; ".join(
            f"{result.subject}: grade {str(result.grade).upper()} gp {float(result.gp):.2f}" if result.gp is not None
            else f"{result.subject}: grade {str(result.grade).upper()}"
            for result in sorted_results
        )
        pass_fail = "FAIL" if any((result.grade or "").upper() == "F" for result in student.results) else "PASS"
        # Add a concise student summary document (no per-subject breakdown)
        documents.append(
            Document(
                page_content=(
                    f"Student {student.name} with USN {student.usn} has SGPA {float(student.sgpa):.2f} "
                    f"and overall status {pass_fail}."
                ),
                metadata={"type": "student_summary", "usn": student.usn, "name": student.name, "pass_fail": pass_fail},
            )
        )
        # Add a detailed per-student document that includes subject-level breakdowns.
        # This document will be skipped for general queries unless the user explicitly
        # asks for grades, subjects, or detailed results.
        documents.append(
            Document(
                page_content=(
                    f"Student {student.name} with USN {student.usn} has SGPA {float(student.sgpa):.2f} "
                    f"and overall status {pass_fail}. Subject details: {result_summary}"
                ),
                metadata={"type": "student_details", "usn": student.usn, "name": student.name, "pass_fail": pass_fail},
            )
        )
    return documents


def _cohort_documents(students: Sequence[Student]) -> List[Document]:
    total_students = len(students)
    if not total_students:
        return []
    average_sgpa = sum(float(student.sgpa) for student in students) / total_students
    topper = max(students, key=lambda student: float(student.sgpa))
    failed_count = sum(
        1 for student in students if any((result.grade or "").upper() == "F" for result in student.results)
    )
    return [
        Document(
            page_content=(
                f"Cohort summary: total students {total_students}. Average SGPA {average_sgpa:.2f}. "
                f"Topper is {topper.name} with USN {topper.usn} and SGPA {float(topper.sgpa):.2f}. "
                f"Students with at least one F grade: {failed_count}."
            ),
            metadata={"type": "cohort_summary"},
        )
    ]


def _schema_documents() -> List[Document]:
    return [
        Document(page_content=description, metadata={"type": "schema"})
        for description in SCHEMA_DESCRIPTIONS
    ]


def build_documents(students: Iterable[Student]) -> List[Document]:
    student_list = list(students)
    return _intent_documents() + _student_documents(student_list) + _cohort_documents(student_list) + _schema_documents()


def _owner_scoped_path(base_path: Path, owner_user_id: int | None) -> Path:
    if owner_user_id is None:
        return base_path
    return base_path.with_name(f"{base_path.stem}_user_{owner_user_id}{base_path.suffix}")


class QueryIntelligenceIndex:
    def __init__(self, owner_user_id: int | None = None) -> None:
        """Index helper for semantic query intelligence.

        If `owner_user_id` is not provided, the current tenant user id from
        the request context will be used.
        """
        self.index_dir = INDEX_DIR
        # Prefer explicit owner_user_id, else fall back to request context
        self.owner_user_id = owner_user_id if owner_user_id is not None else get_current_user_id()
        self.index_file = _owner_scoped_path(INDEX_FILE, self.owner_user_id)
        self.metadata_file = _owner_scoped_path(METADATA_FILE, self.owner_user_id)

    def rebuild(self, students: Iterable[Student]) -> None:
        documents = build_documents(students)
        texts = [document.page_content for document in documents]
        vectors = embed_texts(texts)
        if len(vectors) == 0:
            return

        self.index_dir.mkdir(parents=True, exist_ok=True)
        metadata = []
        with SessionLocal() as db:
            owner_clause = models.SemanticDocument.owner_user_id == self.owner_user_id if self.owner_user_id is not None else models.SemanticDocument.owner_user_id.is_(None)
            db.execute(delete(models.SemanticDocument).where(owner_clause))
            for document, vector in zip(documents, vectors):
                content = document.page_content
                metadata_item = dict(document.metadata or {})
                metadata_item["owner_user_id"] = self.owner_user_id
                semantic_document = models.SemanticDocument(
                    owner_user_id=self.owner_user_id,
                    dataset_id=metadata_item.get("dataset_id"),
                    student_id=metadata_item.get("student_id"),
                    content_type=str(metadata_item.get("type") or "student"),
                    content=content,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    metadata_json=metadata_item,
                    embedding=vector.tolist(),
                )
                db.add(semantic_document)
                metadata.append({"page_content": content, "metadata": metadata_item})
            db.commit()

        self.index_file.write_text(json.dumps({"owner_user_id": self.owner_user_id, "documents": len(metadata)}, indent=2), encoding="utf-8")
        self.metadata_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def exists(self) -> bool:
        with SessionLocal() as db:
            owner_clause = models.SemanticDocument.owner_user_id == self.owner_user_id if self.owner_user_id is not None else models.SemanticDocument.owner_user_id.is_(None)
            return bool(db.scalar(select(models.SemanticDocument.id).where(owner_clause).limit(1)))

    def load(self) -> Tuple[Any, List[Dict[str, object]]]:
        if not self.exists():
            raise FileNotFoundError("Semantic index is not available.")
        metadata = json.loads(self.metadata_file.read_text(encoding="utf-8")) if self.metadata_file.exists() else []
        return None, metadata

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, object]]:
        query_vector = embed_texts([query])[0].tolist()
        hits: List[Dict[str, object]] = []
        with SessionLocal() as db:
            owner_clause = models.SemanticDocument.owner_user_id == self.owner_user_id if self.owner_user_id is not None else models.SemanticDocument.owner_user_id.is_(None)
            documents = list(db.scalars(select(models.SemanticDocument).where(owner_clause)).all())

        if not documents:
            return []

        query_norm = float(np.linalg.norm(np.array(query_vector, dtype="float32"))) or 1.0
        scored_documents: List[Tuple[float, models.SemanticDocument]] = []
        for document in documents:
            embedding_value = document.embedding or []
            if isinstance(embedding_value, str):
                try:
                    embedding_value = json.loads(embedding_value)
                except Exception:
                    embedding_value = []
            vector = np.array(list(embedding_value), dtype="float32")
            if vector.size == 0:
                continue
            similarity = float(np.dot(np.array(query_vector, dtype="float32"), vector) / (query_norm * (float(np.linalg.norm(vector)) or 1.0)))
            scored_documents.append((similarity, document))

        scored_documents.sort(key=lambda item: item[0], reverse=True)
        for score, document in scored_documents[:top_k]:
            hits.append(
                {
                    "score": float(score),
                    "page_content": document.content,
                    "metadata": document.metadata_json or {},
                }
            )
        return hits


def ensure_query_index(students: Iterable[Student], owner_user_id: int | None = None) -> None:
    QueryIntelligenceIndex(owner_user_id=owner_user_id).rebuild(students)


def retrieve_context_documents(query: str, top_k: int = 8, owner_user_id: int | None = None) -> List[Dict[str, object]]:
    try:
        hits = QueryIntelligenceIndex(owner_user_id=owner_user_id).search(query, top_k=top_k)
    except (FileNotFoundError, RuntimeError):
        return []
    documents: List[Dict[str, object]] = []
    seen_keys = set()
    # Decide whether the user's query explicitly requests detailed student results
    detail_keywords = re.compile(r"\b(details|detail|grades|grade|subjects|subject|report|full|complete|detailed|what grades|what is the result)\b", re.I)
    wants_details = bool(detail_keywords.search(query or ""))

    for hit in hits:
        metadata = hit.get("metadata", {})
        if metadata.get("type") == "intent":
            continue
        # By default, skip student_details unless the query asks for details explicitly.
        if metadata.get("type") == "student_details" and not wants_details:
            continue
        dedupe_key = (metadata.get("type"), metadata.get("usn"), hit.get("page_content"))
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        documents.append(hit)
    return documents


def _extract_quoted_value(query: str) -> Optional[str]:
    match = re.search(r'"([^"]+)"|\'([^\']+)\'', query)
    if not match:
        return None
    return next(group for group in match.groups() if group)


def _extract_name_value(query: str) -> Optional[str]:
    def _sanitize_name_candidate(value: str) -> Optional[str]:
        cleaned_value = re.sub(r"\s+", " ", str(value).strip().lower())
        if not cleaned_value:
            return None

        tokens = [token for token in cleaned_value.split() if token]
        leading_noise = {"the", "student", "name", "of", "for", "about"}
        trailing_noise = NON_NAME_QUERY_TERMS | {
            "detail",
            "detailed",
            "details",
            "full",
            "complete",
            "gpa",
            "gp",
            "grade",
            "grades",
            "record",
            "records",
            "report",
            "result",
            "results",
            "sgpa",
        }

        while tokens and tokens[0] in leading_noise:
            tokens.pop(0)
        while tokens and tokens[-1] in trailing_noise:
            tokens.pop()

        if not tokens:
            return None
        if len(tokens) > 6:
            return None
        return " ".join(tokens)

    quoted = _extract_quoted_value(query)
    if quoted:
        return _sanitize_name_candidate(quoted)

    patterns = [
        r"what\s+(?:is\s+)?the\s+(?:sgpa|result)\s+of\s+([a-z][a-z\s\.]+?)[\?\.]?$",
        r"what\s+grades\s+did\s+([a-z][a-z\s\.]+?)\s+receive[\?\.]?$",
        r"show\s+(?:all\s+)?details\s+of\s+([a-z][a-z\s\.]+?)[\?\.]?$",
        r"(?:detailed\s+)?report\s+(?:of|for|about)\s+(?:the\s+)?([a-z][a-z\s\.]+?)[\?\.]?$",
        r"(?:result|details|marks)\s+(?:of|for)\s+([a-z][a-z\s\.]+?)[\?\.]?$",
        r"(?:give|show|tell)(?:\s+me)?\s+(?:the\s+)?(?:detailed\s+)?(?:report|result|details)\s+(?:of|for|about)\s+([a-z][a-z\s\.]+?)[\?\.]?$",
        r"(?:give|show|tell)\s+(?:report|result|details)\s+([a-z][a-z\s\.]+?)[\?\.]?$",
        r"([a-z][a-z\s\.]+?)\s+(?:detailed\s+)?(?:report|result|details|record)\b",
        r"(?:tell\s+me\s+)?(?:something\s+)?about\s+([a-z][a-z\s\.]+?)[\?\.]?$",
        r"(?:who\s+is|about)\s+([a-z][a-z\s\.]+?)[\?\.]?$",
        r"(?:student|name)\s+([a-z][a-z\s\.]+?)[\?\.]?$",
    ]
    lowered = query.lower().strip()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return _sanitize_name_candidate(match.group(1))

    cleaned = lowered
    cleaned = re.sub(
        r"^(?:please\s+)?(?:can\s+you\s+)?(?:give|show|tell)(?:\s+me)?\s+(?:the\s+)?(?:detailed\s+)?(?:report|result|details)\s+(?:of|for|about)\s+",
        "",
        cleaned,
    )
    cleaned = re.sub(r"^(?:please\s+)?(?:can\s+you\s+)?(?:tell\s+me\s+)?(?:something\s+)?about\s+", "", cleaned)
    cleaned = re.sub(r"^(?:student\s+name\s+is\s+|student\s+|name\s+)", "", cleaned)

    bare_name = re.sub(r"[^a-z\s\.]", " ", cleaned)
    bare_name = re.sub(r"\s+", " ", bare_name).strip()
    if bare_name and re.fullmatch(r"[a-z][a-z\s\.]{1,80}", bare_name):
        tokens = [token for token in bare_name.split() if len(token) > 1]
        if (
            1 <= len(tokens) <= 4
            and bare_name not in {"hi", "hello", "hey", "hii", "helloo"}
            and not any(token in NON_NAME_QUERY_TERMS for token in tokens)
        ):
            return _sanitize_name_candidate(bare_name)
    return None


def _extract_grade_value(query: str) -> Optional[str]:
    match = re.search(r"(?<![a-z0-9])(a\+|b\+|o|a|b|c|p|f)(?![a-z0-9])", query.lower())
    if not match:
        return None
    return match.group(1).upper()


def _extract_usn_value(query: str) -> Optional[str]:
    match = re.search(r"\b([0-9][A-Z0-9]{5,})\b", query.upper())
    if not match:
        return None
    return match.group(1).upper()


def _extract_prefix(query: str, target: str) -> Optional[str]:
    patterns = {
        "usn": [
            r"usn\s+prefix\s+([a-z0-9-]+)",
            r"usn\s+(?:starts?|begin)s?\s+with\s+([a-z0-9-]+)",
            r"usn\s+starting\s+with\s+([a-z0-9-]+)",
        ],
        "name": [
            r"name\s+prefix\s+([a-z\s]+)",
            r"name\s+(?:starts?|begin)s?\s+with\s+([a-z\s]+)",
            r"name\s+starting\s+with\s+([a-z\s]+)",
        ],
    }
    normalized = query.lower().strip()
    for pattern in patterns[target]:
        match = re.search(pattern, normalized)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return None


def _extract_limit(query: str) -> int:
    match = re.search(r"\btop\s+(\d+)\b", query.lower())
    if match:
        return max(1, min(25, int(match.group(1))))
    word_map = {"one": 1, "two": 2, "three": 3, "five": 5, "ten": 10}
    for word, value in word_map.items():
        if f"top {word}" in query.lower():
            return value
    return 5


def _extract_subject_phrase_intel(query: str):
    lowered = re.sub(r"\s+", " ", query.lower().strip())
    patterns = [
        r"\b(?:got|gets|gotten|receive(?:d|s)?|earned|has|have)\s+(?:the\s+)?(?:a\+|a|b\+|b|c|p|f|o)\s+grade\s+in\s+(.+?)(?:\s+(?:only|just|please)\b|[\?\.!;,]|$)",
        r"\b([a-z][a-z0-9\s&\-\+]+?)\s+(?:grade|grades|gp|grade point|score)\s+for\s+(.+?)(?:\s+(?:only|just|please)\b|[\?\.!;,]|$)",
        r"\b(?:grade|grades|gp|grade point|score|subject)\s+(?:in|for|of)\s+(.+?)(?:\s+(?:only|just|please)\b|[\?\.!;,]|$)",
        r"\bin\s+(?:the\s+|a\s+|an\s+)?(.+?)(?:\s+(?:only|just|please)\b|[\?\.!;,]|$)",
        r"\bfor\s+(?:the\s+|a\s+|an\s+)?(.+?)(?:\s+(?:only|just|please)\b|[\?\.!;,]|$)",
        r"\bsubject\s+(?:the\s+|a\s+|an\s+)?(.+?)(?:\s+(?:only|just|please)\b|[\?\.!;,]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            phrase = re.sub(r"\s+", " ", match.group(1)).strip()
            phrase = re.sub(r"^(?:the|a|an|of|in)\s+", "", phrase)
            phrase = re.sub(r"\s+(?:subject|course)\s*$", "", phrase)
            if phrase and phrase not in {"here", "there", "subject here", "subject"}:
                return phrase
    return None


def parse_query_entities(query: str, intent: str) -> Dict[str, object]:
    if intent == "GET_RESULT_BY_NAME":
        return {"name": _extract_name_value(query)}
    if intent == "GET_RESULT_BY_USN":
        return {"usn": _extract_usn_value(query)}
    if intent == "GET_STUDENTS_WITH_GRADE":
        entities = {"grade": _extract_grade_value(query)}
        subject_phrase = _extract_subject_phrase_intel(query)
        if subject_phrase:
            entities["subject"] = subject_phrase
        return entities
    if intent in {"GET_FAILED_IN_SUBJECT", "GET_PASSED_IN_SUBJECT"}:
        subject_phrase = _extract_subject_phrase_intel(query)
        return {"subject": subject_phrase} if subject_phrase else {}
    if intent == "GET_USN_PREFIX":
        return {"prefix": _extract_prefix(query, "usn")}
    if intent == "GET_NAME_PREFIX":
        return {"prefix": _extract_prefix(query, "name")}
    if intent == "GET_TOP_N":
        return {"limit": _extract_limit(query)}
    if intent == "GET_GRADE_BUT_FAILED":
        return {"grade": _extract_grade_value(query) or "A+"}
    if intent == "GET_SGPA_RANGE":
        lowered = query.lower()
        match = re.search(r"(?:sgpa|sgpa\s+of)\s*(?:above|greater than|more than|over|>=|at least|from|starting from|starting at|above)\s*([\d.]+)", lowered)
        if not match:
            match = re.search(r"(?:above|greater than|more than|over|at least)\s*([\d.]+)\s*(?:sgpa|gpa)?", lowered)
        if match:
            value = float(match.group(1))
            return {"min_sgpa": value, "max_sgpa": 10.0}
        match = re.search(r"(?:sgpa|sgpa\s+of)\s*(?:below|less than|under|<|at most)\s*([\d.]+)", lowered)
        if not match:
            match = re.search(r"(?:below|less than|under|at most)\s*([\d.]+)\s*(?:sgpa|gpa)?", lowered)
        if match:
            value = float(match.group(1))
            return {"min_sgpa": 0.0, "max_sgpa": value}
        match = re.search(r"(?:sgpa|sgpa\s+of)?\s*(?:between|from)\s*([\d.]+)\s*(?:and|to)\s*([\d.]+)", lowered)
        if match:
            left = float(match.group(1))
            right = float(match.group(2))
            return {"min_sgpa": min(left, right), "max_sgpa": max(left, right)}
    return {}


def validate_entities_for_intent(intent: str, entities: Dict[str, object]) -> tuple[bool, str]:
    """Basic validation for parsed entities to avoid executing obviously invalid structured queries."""
    if not entities:
        return True, ""
    try:
        if intent == "GET_SGPA_RANGE":
            min_sgpa = float(entities.get("min_sgpa", 0.0) or 0.0)
            max_sgpa = float(entities.get("max_sgpa", 10.0) or 10.0)
            if min_sgpa < 0 or max_sgpa > 10 or min_sgpa > max_sgpa:
                return False, "Invalid SGPA range"
        if intent == "GET_TOP_N":
            limit = int(entities.get("limit") or 5)
            if limit < 1 or limit > 100:
                return False, "Limit out of range"
        if intent in {"GET_RESULT_BY_USN", "GET_RESULT_BY_NAME"}:
            # presence checked elsewhere; accept
            return True, ""
        if intent == "GET_STUDENTS_WITH_GRADE":
            grade = str(entities.get("grade") or "").upper()
            if grade and grade not in {"O","A+","A","B+","B","C","P","F"}:
                return False, "Unknown grade"
    except Exception:
        return False, "Entity validation error"
    return True, ""


def _rule_based_intent(query: str) -> Optional[Dict[str, object]]:
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    if not normalized:
        return None

    if re.search(r"\b(best student|topper|who is the topper|highest sgpa)\b", normalized):
        intent = "GET_TOPPER"
    elif "average grade point" in normalized or normalized == "average gp" or "what is the average gp" in normalized:
        intent = "GET_AVERAGE_GP"
    elif "average sgpa" in normalized or "mean sgpa" in normalized or "class average" in normalized:
        intent = "GET_AVERAGE_SGPA"
    elif re.search(r"\btop\s+(\d+|one|two|three|five|ten)\b", normalized):
        intent = "GET_TOP_N"
    elif "which grade appears most frequently" in normalized or "most common grade" in normalized:
        intent = "GET_MOST_FREQUENT_GRADE"
    elif "count total number of students" in normalized or "total students in the file" in normalized or "how many students are there" in normalized:
        intent = "GET_TOTAL_STUDENTS"
    elif "how many students failed" in normalized or "count failed students" in normalized or "number of failures" in normalized:
        intent = "GET_FAILED_COUNT"
    elif "pass percentage" in normalized or "percentage of students passed" in normalized or "percentage pass" in normalized or "how many percent passed" in normalized or ("pass" in normalized and "percentage" in normalized):
        intent = "GET_PASS_PERCENTAGE"
    elif "list all subjects" in normalized or "show all subjects" in normalized or normalized in {"list subjects", "subjects list"} or ("all subjects" in normalized and "student" not in normalized):
        intent = "GET_ALL_SUBJECTS"
    elif "all students and their usns" in normalized or normalized == "show all students" or "list all student records" in normalized:
        intent = "GET_ALL_STUDENTS"
    elif "all passing grades" in normalized or "students with no f" in normalized or "all pass students" in normalized:
        intent = "GET_ALL_PASSING"
    elif re.search(r"\b(?:sgpa|gpa)\b", normalized) and re.search(r"\b(?:more than|greater than|above|over|at least|less than|below|under|between|from|to|>=|<=|>|<)\b", normalized):
        intent = "GET_SGPA_RANGE"
    elif "gp = 0 but also" in normalized or "gp 0 but also" in normalized:
        intent = "GET_GP_ZERO_WITH_A"
    elif ("gp is 0" in normalized or "gp zero" in normalized or "students with gp 0" in normalized) and "also" not in normalized:
        intent = "GET_GP_ZERO_ANY"
    elif ("failed in another" in normalized or "failed another" in normalized) and ("a+" in normalized or "a grade" in normalized or "grade a" in normalized):
        intent = "GET_GRADE_BUT_FAILED"
    elif re.search(r"(?:failed|fail)\s+(?:in|on)\s+(?:the\s+)?", normalized) and _extract_subject_phrase_intel(query):
        intent = "GET_FAILED_IN_SUBJECT"
    elif re.search(r"(?:passed?|pass)\s+(?:in|on)\s+(?:the\s+)?", normalized) and _extract_subject_phrase_intel(query):
        intent = "GET_PASSED_IN_SUBJECT"
    elif "who failed" in normalized:
        intent = "GET_FAILED"
    elif _extract_prefix(query, "usn"):
        intent = "GET_USN_PREFIX"
    elif _extract_prefix(query, "name"):
        intent = "GET_NAME_PREFIX"
    elif _extract_usn_value(query) and "prefix" not in normalized and "starts with" not in normalized:
        intent = "GET_RESULT_BY_USN"
    elif _extract_grade_value(query) and (
        "students with grade" in normalized
        or "students with " in normalized
        or "got f grade" in normalized
        or "got the" in normalized
        or re.search(r"\bgot\s+(?:the\s+)?(?:a\+|a|b\+|b|c|p|f|o)\s+grade\b", normalized)
        or re.search(r"\b(?:a\+|a|b\+|b|c|p|f|o)\s+(?:grade|in)\b", normalized)
    ):
        intent = "GET_STUDENTS_WITH_GRADE"
    elif _extract_name_value(query):
        intent = "GET_RESULT_BY_NAME"
    else:
        return None

    return {
        "intent": intent,
        "confidence": 1.0,
        "suggestions": [],
        "entities": parse_query_entities(query, intent),
        "source": "rule",
    }


def _llm_based_intent(query: str, history: Optional[Sequence[Dict[str, object]]] = None) -> Optional[Dict[str, object]]:
    catalog = {
        intent: {
            "description": config["description"],
            "examples": config["examples"][:3],
        }
        for intent, config in INTENT_LIBRARY.items()
    }
    system_prompt = (
        "You map student-analytics questions into one supported intent and entities. "
        "Return only JSON with keys intent, confidence, entities, suggestions. "
        "Use null intent if the question is outside the supported intents. "
        "For open-ended questions like summaries, insights, comparisons, explanations, or class overviews, "
        "prefer null intent so the downstream RAG answerer can handle them. "
        "Keep confidence between 0 and 1."
    )
    user_prompt = json.dumps(
        {
            "supported_intents": catalog,
            "entity_rules": {
                "GET_RESULT_BY_NAME": {"name": "student name"},
                "GET_RESULT_BY_USN": {"usn": "exact student usn"},
                "GET_STUDENTS_WITH_GRADE": {"grade": "one of O, A+, A, B+, B, C, P, F"},
                "GET_USN_PREFIX": {"prefix": "usn prefix"},
                "GET_NAME_PREFIX": {"prefix": "name prefix"},
                "GET_TOP_N": {"limit": "integer"},
                "GET_GRADE_BUT_FAILED": {"grade": "grade, default A+"},
            },
            "prefer_contextual_rag_for": [
                "Summarize this class",
                "Give me insights about the class performance",
                "Compare toppers and failed students",
                "Explain the overall result trend",
            ],
            "query": query,
        },
        ensure_ascii=True,
    )
    parsed = _llm_chat_json(system_prompt, user_prompt, history=history)
    if not parsed:
        return None
    # If Gemini returned a wrapper with an 'answer' string, try to extract
    # a JSON object from that text.
    if isinstance(parsed, dict) and parsed.get("intent") is None:
        # Attempt to extract JSON from common keys such as 'answer' or 'text'
        candidate_text = None
        for k in ("intent", "answer", "text", "message"):
            if isinstance(parsed.get(k), str) and parsed.get(k).strip():
                candidate_text = parsed.get(k)
                break
        if candidate_text:
            extracted = _extract_json_object(candidate_text)
            if isinstance(extracted, dict):
                parsed = extracted

    intent = parsed.get("intent")
    if intent is None:
        return None
    intent_str = str(intent).strip().upper()
    if intent_str not in INTENT_LIBRARY:
        return None

    confidence_value = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence_value)
    except (TypeError, ValueError):
        confidence = 0.0

    entities = parsed.get("entities", {})
    if not isinstance(entities, dict):
        entities = {}

    suggestions = parsed.get("suggestions", [])
    if not isinstance(suggestions, list):
        suggestions = []

    merged_entities = parse_query_entities(query, intent_str)
    merged_entities.update({key: value for key, value in entities.items() if value not in (None, "")})

    if confidence < 0.35:
        return None

    return {
        "intent": intent_str,
        "confidence": confidence,
        "suggestions": [str(item) for item in suggestions[:3]],
        "entities": merged_entities,
        "source": "llm",
    }


def answer_query_from_context(
    query: str,
    context: Dict[str, object],
    history: Optional[Sequence[Dict[str, object]]] = None,
) -> Optional[Dict[str, object]]:
    system_prompt = (
        "You are a grounded analytics assistant for uploaded student result documents. "
        "Answer any question about the uploaded dataset using only the provided database-backed context. "
        "If the query is outside the student results scope, return an unsupported answer with empty citations and student_usns. "
        "The context may include summary metrics, matching student records, matching result rows, retrieved text chunks, "
        "and recent conversation focus. Prefer exact database-supported answers over generic language. "
        "If the query is ambiguous, answer with the best grounded interpretation and say what you matched. "
        "If there are multiple relevant subject rows, mention them clearly instead of guessing one. "
        "Use the conversation history for follow-up references like 'he', 'that student', 'those grades', or 'that subject'. "
        "If the answer is not supported by context, say that explicitly and do not invent details. "
        "Every answer must cite the dataset evidence it used. If you cannot cite supporting evidence from the provided context, return an unsupported answer. "
        "Use the dataset's metric names exactly: SGPA for semester performance, GP for subject-level grade points, and grade/pass_fail for letter outcomes. "
        "If the user says GPA, clarify whether you mean SGPA or GP based on the available context rather than collapsing them into one term. "
        "Return only JSON with keys answer, student_usns, confidence, citations."
    )
    user_prompt = json.dumps({"query": query, "context": context}, ensure_ascii=True)
    parsed = _llm_chat_json(system_prompt, user_prompt, history=history)
    if not parsed:
        return None

    answer = str(parsed.get("answer", "")).strip()
    if not answer:
        return None

    usns = parsed.get("student_usns", [])
    if not isinstance(usns, list):
        usns = []
    citations_raw = parsed.get("citations", [])
    citations = [str(item).strip() for item in citations_raw if str(item).strip()] if isinstance(citations_raw, list) else []
    confidence_value = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence_value)
    except (TypeError, ValueError):
        confidence = 0.0

    if not citations and not usns:
        return None

    return {
        "answer": answer,
        "student_usns": [str(usn).upper() for usn in usns if str(usn).strip()],
        "confidence": confidence,
        "citations": citations[:6],
    }


def detect_intent(query: str, history: Optional[Sequence[Dict[str, object]]] = None, owner_user_id: int | None = None) -> Dict[str, object]:
    if not query.strip():
        return {
            "intent": None,
            "confidence": 0.0,
            "source": "none",
            "suggestions": [
                "Try queries like 'topper', 'who failed', 'students with A+', or 'result of Abir'."
            ],
        }

    direct_match = _rule_based_intent(query)
    if direct_match:
        return direct_match

    llm_match = _llm_based_intent(query, history=history)
    if llm_match:
        return llm_match

    try:
        hits = QueryIntelligenceIndex(owner_user_id=owner_user_id).search(query, top_k=5)
    except (FileNotFoundError, RuntimeError):
        hits = []
    intent_hits = [hit for hit in hits if hit["metadata"].get("type") == "intent"]
    if not intent_hits:
        return {
            "intent": None,
            "confidence": 0.0,
            "source": "semantic",
            "suggestions": [
                "Try queries like 'top 5 students', 'average SGPA', or 'USN prefix 1MS22'."
            ],
        }

    best_hit = intent_hits[0]
    intent = str(best_hit["metadata"].get("intent"))
    confidence = float(best_hit["score"])
    suggestions = [str(hit["metadata"].get("example")) for hit in intent_hits[:3]]

    if confidence < SEMANTIC_FALLBACK_THRESHOLD:
        return {
            "intent": None,
            "confidence": confidence,
            "source": "semantic",
            "suggestions": suggestions,
        }

    return {
        "intent": intent,
        "confidence": confidence,
        "source": "semantic",
        "suggestions": suggestions,
        "entities": parse_query_entities(query, intent),
    }


def plan_query_mode(query: str, history: Optional[Sequence[Dict[str, object]]] = None) -> Dict[str, object]:
    """Plan retrieval mode for query execution.

    Modes:
    - sql: primarily structured facts/aggregations from DB tables.
    - semantic: primarily contextual/semantic retrieval from vector index.
    - hybrid: combine DB and semantic evidence.
    """
    normalized = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not normalized:
        return {"mode": "hybrid", "confidence": 0.0, "reason": "empty_query"}

    # Fast deterministic shortcuts for high-signal patterns.
    sql_markers = [
        "student",
        "students",
        "usn",
        "sgpa",
        "gp",
        "grade",
        "grades",
        "subject",
        "subjects",
        "pass",
        "fail",
        "topper",
        "top ",
        "average",
        "count",
        "how many",
        "percentage",
        "percent",
    ]
    semantic_markers = [
        "summarize",
        "summary",
        "insight",
        "insights",
        "trend",
        "trends",
        "why",
        "explain",
        "compare",
        "overall",
    ]

    has_sql = any(marker in normalized for marker in sql_markers)
    has_semantic = any(marker in normalized for marker in semantic_markers)

    if has_sql and has_semantic:
        return {"mode": "hybrid", "confidence": 0.9, "reason": "mixed_signals"}
    if has_sql and not has_semantic:
        return {"mode": "sql", "confidence": 0.85, "reason": "structured_signal"}
    if has_semantic and not has_sql:
        return {"mode": "semantic", "confidence": 0.85, "reason": "semantic_signal"}

    # Gemini planner fallback for ambiguous questions.
    system_prompt = (
        "You route student-analytics questions to retrieval modes. "
        "Return strict JSON only with keys mode, confidence, reason. "
        "mode must be one of: sql, semantic, hybrid. "
        "Use sql for exact factual lookups/filters/aggregations from tables; "
        "semantic for open-ended contextual interpretation; "
        "hybrid when both are needed."
    )
    user_prompt = json.dumps({"query": query}, ensure_ascii=True)
    parsed = _llm_chat_json(system_prompt, user_prompt, history=history)
    if isinstance(parsed, dict):
        mode = str(parsed.get("mode", "")).strip().lower()
        if mode in {"sql", "semantic", "hybrid"}:
            try:
                confidence = float(parsed.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            return {
                "mode": mode,
                "confidence": max(0.0, min(confidence, 1.0)),
                "reason": str(parsed.get("reason", "llm_planner") or "llm_planner"),
            }

    return {"mode": "hybrid", "confidence": 0.5, "reason": "fallback"}
