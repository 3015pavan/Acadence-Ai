import hashlib
import logging
import json
import re
import time
import statistics
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from .analyzer import (
    build_results_dataframe,
    build_students_dataframe,
    compute_average_gp,
    compute_average_sgpa,
    compute_grade_distribution,
    fetch_students,
    fetch_student_by_usn,
    fetch_students_by_usns,
    fetch_top_students,
    fetch_topper,
    serialize_student,
)
from .cache import get_cached_query, set_cached_query
from .elastic import (
    get_elasticsearch_client,
    search_students_by_name_ranked,
)
from .intelligence import (
    QueryIntelligenceIndex,
    _extract_grade_value,
    _extract_name_value,
    _extract_subject_phrase_intel,
    _extract_usn_value,
    answer_query_from_context,
    detect_intent,
    plan_query_mode,
    retrieve_context_documents,
    validate_entities_for_intent,
)
from ..utils.monitoring import send_alert


SUPPORTED_QUERY_HINTS = [
    "topper",
    "who failed",
    "failed in a subject",
    "students with F in a subject",
    "who did not pass a subject",
    "result of Abir",
    "students with A+",
    "students with A+ but failed in another subject",
    "inconsistent performers",
    "GP = 0 but also A grades",
    "average SGPA",
    "average GP",
    "list all subjects",
    "top 5 students",
]

STUDENT_QUERY_STOPWORDS = {
    "about",
    "and",
    "did",
    "for",
    "get",
    "got",
    "grade",
    "grades",
    "gradepoint",
    "gradepoints",
    "grade point",
    "grade points",
    "gp",
    "in",
    "marks",
    "of",
    "point",
    "points",
    "score",
    "scores",
    "sgpa",
    "student",
    "subject",
    "the",
    "usn",
    "what",
    "which",
    "with",
}

SUBJECT_QUERY_STOPWORDS = STUDENT_QUERY_STOPWORDS | {
    "cgpa",
    "did",
    "get",
    "got",
    "has",
    "have",
    "his",
    "her",
    "is",
    "me",
    "name",
    "studentname",
    "this",
    "that",
    "tell",
    "their",
}

INTENT_TO_QUERY_TYPE = {
    "GET_TOPPER": "aggregation",
    "GET_AVERAGE_SGPA": "aggregation",
    "GET_TOP_N": "aggregation",
    "GET_RESULT_BY_NAME": "lookup",
    "GET_RESULT_BY_USN": "lookup",
    "GET_USN_PREFIX": "lookup",
    "GET_NAME_PREFIX": "lookup",
    "GET_SUBJECTS_WITH_GRADE": "lookup",
    "GET_FAILED": "filter",
    "GET_FAILED_IN_SUBJECT": "filter",
    "GET_PASSED_IN_SUBJECT": "filter",
    "GET_SGPA_RANGE": "filter",
    "GET_STUDENTS_WITH_GRADE": "filter",
    "GET_GRADE_BUT_FAILED": "filter",
    "GET_INCONSISTENT_PERFORMERS": "filter",
    "GET_GP_ZERO_WITH_A": "filter",
    "GET_GP_ZERO_ANY": "filter",
    "GET_ALL_STUDENTS": "filter",
    "GET_ALL_PASSING": "filter",
    "GET_FAILED_COUNT": "aggregation",
    "GET_TOTAL_STUDENTS": "aggregation",
    "GET_MOST_FREQUENT_GRADE": "aggregation",
    "GET_AVERAGE_GP": "aggregation",
    "GET_ALL_SUBJECTS": "aggregation",
    "GET_PASS_PERCENTAGE": "aggregation",
}

CACHEABLE_INTENTS = {"GET_TOPPER", "GET_AVERAGE_SGPA", "GET_TOTAL_STUDENTS", "GET_FAILED_COUNT", "GET_ALL_SUBJECTS", "GET_PASS_PERCENTAGE", "GET_AVERAGE_GP"}


# Simple in-memory metrics to track query routing and cache usage.
_QUERY_METRICS = {
    "total_queries": 0,
    "llm_used": 0,
    "structured_used": 0,
    "cache_hits": 0,
}


def _sanitize_query(raw: str) -> Optional[str]:
    if raw is None:
        return None
    # Remove control characters and trim
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", str(raw)).strip()
    # Enforce reasonable length
    if len(cleaned) == 0:
        return None
    if len(cleaned) > 2000:
        cleaned = cleaned[:2000]
    # Reject obviously dangerous tokens (no direct SQL should be executed anywhere)
    if ";" in cleaned or "--" in cleaned:
        logging.warning("Query contains suspicious punctuation and will be sanitized: %s", cleaned[:200])
        cleaned = cleaned.replace(";", " ").replace("--", " ")
    return cleaned


def _cache_key(query: str, owner_user_id: Optional[int] = None) -> str:
    owner_suffix = f":{owner_user_id}" if owner_user_id is not None else ":public"
    digest = hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()
    return f"student-query{owner_suffix}:{digest}"


def _empty_response(message: str, *, suggestions: Optional[List[str]] = None, intent: Optional[str] = None, meta: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    return {
        "intent": intent,
        "answer": message,
        "students": [],
        "meta": meta or {},
        "suggestions": suggestions or SUPPORTED_QUERY_HINTS[:4],
    }


def _student_response(intent: str, answer: str, students: Sequence[object], meta: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    include_details = bool(meta.get("include_details")) if isinstance(meta, dict) else False
    return {
        "intent": intent,
        "answer": answer,
        "students": [
            serialize_student(student) if include_details else {
                "usn": student.usn,
                "name": student.name,
                "sgpa": float(student.sgpa),
                "pass_fail": "FAIL" if any((result.grade or "").upper() == "F" for result in getattr(student, "results", []) or []) else "PASS",
            }
            for student in students
        ],
        "meta": meta or {},
        "suggestions": [],
    }


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _is_greeting(query: str) -> bool:
    return _normalize_text(query) in {"hi", "hello", "hey", "hii", "helloo"}


def _is_multi_student_request(query: str) -> bool:
    lowered = query.lower()
    markers = [
        "all students",
        "students who",
        "students with",
        "list students",
        "show students",
        "find students",
        "who failed",
        "failed students",
        "top ",
    ]
    return any(marker in lowered for marker in markers)


def _is_generic_subject_phrase(phrase: str) -> bool:
    normalized = _normalize_text(phrase)
    return normalized in {
        "a subject",
        "any subject",
        "all subjects",
        "every subject",
        "the subject",
        "one subject",
        "subject list",
        "list",
        "any",
        "all",
    }


def _has_student_reference(query: str) -> bool:
    if _extract_usn_value(query) or _extract_name_value(query):
        return True
    lowered = query.lower()
    subject_phrase = _extract_subject_phrase(query)
    if subject_phrase and any(marker in lowered for marker in ["grade", "grades", "gp", "grade point", "score", "marks", "sgpa"]):
        return True
    return any(
        token in lowered
        for token in [" his ", " her ", " their ", " he ", " she ", " that student", " this student"]
    )


def _query_words(query: str) -> List[str]:
    return [token for token in _normalize_text(query).split() if token]


def _student_name(student: object) -> str:
    return str(student.get("name", "")) if isinstance(student, dict) else str(student.name)


def _student_usn(student: object) -> str:
    return str(student.get("usn", "")) if isinstance(student, dict) else str(student.usn)


def _student_sgpa(student: object) -> float:
    value = student.get("sgpa", 0.0) if isinstance(student, dict) else student.sgpa
    return float(value or 0.0)


def _student_results(student: object) -> List[object]:
    return list(student.get("results", [])) if isinstance(student, dict) else list(student.results)


def _result_subject(result: object) -> str:
    return str(result.get("subject", "")) if isinstance(result, dict) else str(result.subject)


def _result_grade(result: object) -> str:
    return str(result.get("grade", "")) if isinstance(result, dict) else str(result.grade)


def _result_gp(result: object) -> Optional[float]:
    value = result.get("gp") if isinstance(result, dict) else result.gp
    return float(value) if value is not None else None


def _should_prefer_contextual_answer(query: str) -> bool:
    normalized = _normalize_text(query)
    if not normalized:
        return False

    contextual_phrases = [
        "summarize this class",
        "summarize the class",
        "class summary",
        "class overview",
        "overall performance",
        "overall result",
        "cohort summary",
        "dataset summary",
        "give insights",
        "show insights",
        "performance insights",
        "result trends",
        "performance trends",
        "compare",
        "explain",
        "tell me about",
        "what about",
        "how about",
        "can you summarize",
        "can you explain",
        "why did",
        "how did",
    ]
    structured_markers = [
        "result of",
        "details of",
        "marks for",
        "usn prefix",
        "name prefix",
        "top ",
        "average sgpa",
        "average gp",
        "who failed",
        "students with ",
    ]

    if any(marker in normalized for marker in structured_markers):
        return False
    return any(phrase in normalized for phrase in contextual_phrases)


def _should_try_contextual_first(query: str) -> bool:
    normalized = _normalize_text(query)
    if not normalized:
        return False

    words = _query_words(query)
    open_ended_markers = [
        "analyze",
        "analysis",
        "compare",
        "explain",
        "tell me about",
        "what about",
        "how about",
        "can you",
        "insight",
        "insights",
        "overall",
        "pattern",
        "patterns",
        "summarize",
        "summary",
        "trend",
        "trends",
        "why",
    ]
    exact_shortcuts = {
        "topper",
        "who failed",
        "average sgpa",
        "average gp",
        "show all students",
    }

    if normalized in exact_shortcuts:
        return False
    if re.search(r"\btop\s+(\d+|one|two|three|five|ten)\b", normalized):
        return False
    if "topper" in normalized or "top " in normalized or "rank" in normalized:
        return False
    if any(marker in normalized for marker in open_ended_markers):
        return True
    if "?" in query and len(words) >= 4:
        return True
    if len(words) >= 7:
        return True
    return False


def _is_dataset_related_query(query: str) -> bool:
    normalized = _normalize_text(query)
    if not normalized:
        return False
    if _is_greeting(query):
        return True
    if _extract_usn_value(query) or _extract_name_value(query):
        return True
    if _extract_subject_phrase(query) or _is_strict_structured_query(query):
        return True

    dataset_keywords = [
        "student",
        "students",
        "usn",
        "sgpa",
        "gp",
        "grade",
        "grades",
        "subject",
        "subjects",
        "result",
        "results",
        "topper",
        "top",
        "failed",
        "fail",
        "pass",
        "passed",
        "rank",
        "average",
        "percentage",
        "semester",
        "class",
        "cohort",
        "performance",
        "insight",
        "insights",
        "trend",
        "trends",
        "summary",
        "summarize",
        "overview",
        "marks",
    ]
    return any(keyword in normalized for keyword in dataset_keywords)


def _is_strict_structured_query(query: str) -> bool:
    normalized = _normalize_text(query)
    if not normalized:
        return False

    strict_markers = [
        "result of",
        "details of",
        "detailed report of",
        "report of",
        "marks for",
        "show ",
        "usn prefix",
        "name prefix",
        "topper",
        "top ",
        "average sgpa",
        "average gp",
        "who failed",
        "students with ",
        "failed in",
        "passed in",
        "sgpa above",
        "sgpa below",
        "pass percentage",
    ]
    return any(marker in normalized for marker in strict_markers)


def _infer_usns_from_matching_rows(context: Dict[str, object], limit: int = 3) -> List[str]:
    rows = context.get("matching_results", [])
    owner_user_id: Optional[int] = None
    if not isinstance(rows, list) or not rows:
        return []

    score: Dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        usn = str(row.get("usn", "")).upper().strip()
        if not usn:
            continue
        score[usn] = score.get(usn, 0) + 1

    ranked = sorted(score.items(), key=lambda item: item[1], reverse=True)
    return [usn for usn, _ in ranked[:limit]]


def _normalize_scores(values: Dict[str, float]) -> Dict[str, float]:
    if not values:
        return {}
    maximum = max(values.values())
    minimum = min(values.values())
    if maximum == minimum:
        return {key: 1.0 for key in values}
    return {key: (value - minimum) / (maximum - minimum) for key, value in values.items()}


def _hybrid_lookup_by_name(db: Session, query: str, student_name: str, limit: int = 10, owner_user_id: Optional[int] = None) -> Dict[str, object]:
    es_scores: Dict[str, float] = {}
    try:
        elastic_client = get_elasticsearch_client()
        es_hits = search_students_by_name_ranked(elastic_client, student_name, limit=limit, owner_user_id=owner_user_id)
        es_scores = _normalize_scores({hit["usn"]: float(hit.get("score") or 0.0) for hit in es_hits})
    except Exception as exc:
        # Elasticsearch may be down or unreachable; continue with semantic/local fallbacks.
        logging.warning("Elasticsearch lookup failed, falling back to semantic/local search: %s", exc)
        es_scores = {}

    semantic_scores: Dict[str, float] = {}
    try:
        semantic_hits = QueryIntelligenceIndex(owner_user_id=owner_user_id).search(query, top_k=20)
        # Accept both student summary and detailed student documents from the semantic store.
        student_hits = [hit for hit in semantic_hits if str(hit["metadata"].get("type", "")).startswith("student")]
        semantic_scores = _normalize_scores(
            {
                str(hit["metadata"].get("usn")): float(hit["score"])
                for hit in student_hits
                if hit["metadata"].get("usn")
            }
        )
    except Exception:
        semantic_scores = {}

    combined: Dict[str, float] = {}
    for usn in set([*es_scores.keys(), *semantic_scores.keys()]):
        combined[usn] = 0.65 * es_scores.get(usn, 0.0) + 0.35 * semantic_scores.get(usn, 0.0)

    ranked_usns = [item[0] for item in sorted(combined.items(), key=lambda item: item[1], reverse=True)]
    students = fetch_students_by_usns(db, ranked_usns, owner_user_id=owner_user_id)
    return {
        "students": students,
        "meta": {
            "hybrid_scores": {student.usn: round(combined.get(student.usn, 0.0), 4) for student in students},
            "es_candidates": len(es_scores),
            "semantic_candidates": len(semantic_scores),
        },
    }


def _plan_query(intent: str) -> str:
    return INTENT_TO_QUERY_TYPE.get(intent, "filter")


def classify_query_type(query: str, history: Optional[Sequence[Dict[str, object]]] = None) -> Optional[str]:
    """Classify at a high level whether a query is best handled as lookup/aggregation/filter/contextual.

    This uses lightweight heuristics and existing intent detection as a fallback.
    """
    if not query or not query.strip():
        return None
    if _is_greeting(query):
        return "chat"
    # If user explicitly provides USN or quoted name, it's a lookup
    if _extract_usn_value(query) or _extract_name_value(query):
        return "lookup"
    # Subject-level structured queries
    if _is_strict_structured_query(query) or _execute_subject_result_query is not None and _extract_subject_phrase(query):
        # Could be lookup or filter depending on phrasing
        subject = _extract_subject_phrase(query)
        if subject:
            # If asking for a single student + subject, it's a lookup; multi-student markers -> filter
            if _has_student_reference(query):
                return "lookup"
            return "filter"
    # Use heuristics for contextual vs structured
    if _should_try_contextual_first(query) or _should_prefer_contextual_answer(query):
        return "contextual"

    # Fallback to intent detection which maps to types
    intent_result = detect_intent(query, history=history, owner_user_id=owner_user_id)
    intent = intent_result.get("intent")
    if intent:
        return _plan_query(str(intent))
    return None


def _filter_students_via_postgres(db: Session, usns: Sequence[str]) -> List[object]:
    return fetch_students_by_usns(db, usns) if usns else []


def _students_from_dataframe(db: Session, dataframe: pd.DataFrame) -> List[object]:
    usns = dataframe["usn"].dropna().astype(str).tolist() if not dataframe.empty and "usn" in dataframe.columns else []
    return _filter_students_via_postgres(db, usns)


def _latest_history_students(db: Session, history: Optional[Sequence[Dict[str, object]]] = None, owner_user_id: Optional[int] = None) -> List[object]:
    recent_usns: List[str] = []
    for item in reversed(list(history or [])):
        usns = item.get("student_usns", []) if isinstance(item, dict) else []
        for usn in usns:
            normalized = str(usn).upper()
            if normalized and normalized not in recent_usns:
                recent_usns.append(normalized)
        if recent_usns:
            break
    return fetch_students_by_usns(db, recent_usns, owner_user_id=owner_user_id)


def _is_followup_query(query: str) -> bool:
    normalized = _normalize_text(query)
    followup_markers = {
        "he",
        "she",
        "his",
        "her",
        "their",
        "that student",
        "this student",
        "same student",
        "those students",
        "that subject",
        "same subject",
        "above student",
        "previous student",
    }
    return any(marker in normalized for marker in followup_markers)


def _find_students_from_query_or_history(db: Session, query: str, history: Optional[Sequence[Dict[str, object]]] = None, owner_user_id: Optional[int] = None) -> List[object]:
    usn_match = re.search(r"\b[0-9][A-Z0-9]{5,}\b", query.upper())
    if usn_match:
        student = fetch_student_by_usn(db, usn_match.group(0), owner_user_id=owner_user_id)
        return [student] if student else []

    students = fetch_students(db, owner_user_id=owner_user_id)
    normalized_query = _normalize_text(query)
    subject_phrase = _extract_subject_phrase(query)
    student_query = normalized_query
    if subject_phrase:
        student_query = student_query.replace(_normalize_text(subject_phrase), " ")
    student_query = re.sub(r"\b[0-9][a-z0-9]{5,}\b", " ", student_query)
    student_query = re.sub(
        r"\b(grade|grades|grade point|gradepoint|gp|point|points|score|scores|sgpa|subject|in|for|of|the|and|what|which|about|his|her|did|get|got|student|usn|with|thre|three)\b",
        " ",
        student_query,
    )
    student_query = re.sub(r"\s+", " ", student_query).strip()
    query_tokens = [token for token in student_query.split() if len(token) > 2]
    history_students = _latest_history_students(db, history, owner_user_id=owner_user_id)

    exact_name_matches = [student for student in students if _normalize_text(student.name) in student_query]
    if exact_name_matches:
        return exact_name_matches

    if history_students and query_tokens:
        for student in history_students:
            history_name_tokens = [token for token in _normalize_text(student.name).split() if len(token) > 2]
            overlap = sum(1 for token in query_tokens if any(token in name_token or name_token in token for name_token in history_name_tokens))
            if overlap >= 1:
                return [student]

    if query_tokens:
        contains_all_token_matches = [
            student
            for student in students
            if all(token in _normalize_text(student.name) for token in query_tokens)
        ]
        if len(contains_all_token_matches) == 1:
            return contains_all_token_matches

        scored = []
        for student in students:
            name_tokens = _normalize_text(student.name).split()
            score = sum(
                1
                for token in query_tokens
                if any(
                    len(name_token) > 1 and (token in name_token or (len(token) > 3 and name_token in token))
                    for name_token in name_tokens
                )
            )
            if score > 0:
                scored.append((score, float(student.sgpa), student))
        if scored:
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            best_score = scored[0][0]
            best_students = [student for score, _, student in scored if score == best_score]
            if best_score >= 2:
                return best_students[:3]
            if best_score >= 1 and len(best_students) == 1 and len(query_tokens) <= 2:
                return best_students[:1]

    student_like_phrase = student_query
    if student_like_phrase:
        fuzzy_scored = []
        for student in students:
            ratio = SequenceMatcher(None, student_like_phrase, _normalize_text(student.name)).ratio()
            if ratio >= 0.62:
                fuzzy_scored.append((ratio, float(student.sgpa), student))
        if fuzzy_scored:
            fuzzy_scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return [fuzzy_scored[0][2]]

    if history_students and _is_followup_query(query):
        return history_students

    return []


def _query_tokens(text: str, stopwords: set[str]) -> List[str]:
    return [
        token
        for token in _normalize_text(text).split()
        if len(token) > 1 and token not in stopwords
    ]


def _extract_subject_phrase(query: str) -> Optional[str]:
    lowered = query.lower().strip()
    patterns = [
        r"\b([a-z][a-z0-9\s&\-\+]+?)\s+(?:grade|grades|gp|grade point|score)\s+for\s+[a-z0-9 ]+[\?\.]?$",
        r"\b(?:grade|grades|gp|grade point|score|subject)\s+(?:in|for|of)\s+([a-z][a-z0-9\s&\-\+]+?)(?:\s+for|\s+of|\s+by|\s+student|\s+usn|[\?\.]?$)",
        r"\bin\s+([a-z][a-z0-9\s&\-\+]+?)(?:\s+subject)?[\?\.]?$",
        r"\bfor\s+([a-z][a-z0-9\s&\-\+]+?)(?:\s+subject)?[\?\.]?$",
        r"\bsubject\s+([a-z][a-z0-9\s&\-\+]+?)(?:\s+for|\s+of|\s+by|\s+student|\s+usn|[\?\.]?$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            phrase = re.sub(r"\s+", " ", match.group(1)).strip()
            if phrase:
                return phrase
    return None


def _build_subject_query_tokens(query: str, student: Optional[object] = None) -> List[str]:
    normalized_query = _normalize_text(query)
    subject_phrase = _extract_subject_phrase(query)
    if subject_phrase:
        tokens = _query_tokens(subject_phrase, SUBJECT_QUERY_STOPWORDS)
        if tokens:
            return tokens

    working_query = normalized_query
    if student is not None:
        working_query = working_query.replace(_normalize_text(_student_name(student)), " ")
        working_query = working_query.replace(_student_usn(student).lower(), " ")
    return _query_tokens(working_query, SUBJECT_QUERY_STOPWORDS)


def _find_subject_results(student: object, query: str) -> List[object]:
    normalized_query = _normalize_text(query)
    subject_phrase = _extract_subject_phrase(query)
    subject_tokens = _build_subject_query_tokens(query, student=student)
    results = _student_results(student)
    if not results:
        return []

    scored_results = []
    for result in results:
        subject_text = _normalize_text(_result_subject(result) or "")
        score = 0
        tokens_to_check = subject_tokens if subject_tokens else normalized_query.split()
        for token in tokens_to_check:
            if len(token) <= 2 or token in SUBJECT_QUERY_STOPWORDS:
                continue
            if token in subject_text:
                score += 2
        if subject_phrase:
            phrase_text = _normalize_text(subject_phrase)
            if phrase_text and phrase_text in subject_text:
                score += 10
            else:
                phrase_tokens = [token for token in phrase_text.split() if len(token) > 2]
                score += sum(3 for token in phrase_tokens if token in subject_text)
        if subject_phrase and _normalize_text(subject_phrase) == subject_text:
            score += 15
        if score > 0:
            scored_results.append((score, result))

    if not scored_results:
        return []
    scored_results.sort(key=lambda item: (item[0], len(_normalize_text(_result_subject(item[1])))), reverse=True)
    best_score = scored_results[0][0]
    return [result for score, result in scored_results if score == best_score][:3]


def _execute_subject_for_grade_query(db: Session, query: str, history: Optional[Sequence[Dict[str, object]]] = None, owner_user_id: Optional[int] = None) -> Optional[Dict[str, object]]:
    """Handle reverse subject lookup: 'In which subject did [student] get [grade]?'
    
    Regex-based extraction of student (name/USN) and grade, then database lookup.
    """
    normalized_query = _normalize_text(query)
    
    # Handle reverse lookup styles like:
    # "in which subject ... got O grade", "where ... got A grade", "list subject(s) ... got A+"
    asks_subject_lookup = (
        "subject" in normalized_query
        and any(marker in normalized_query for marker in ["which", "what", "where", "list"])
    )
    if not asks_subject_lookup:
        return None
    if not any(word in normalized_query for word in ["grade", "garde", "got", "get", "earned", "received"]):
        return None
    
    # Extract grade using existing helper
    grade = _extract_grade_value(query)
    if not grade:
        return None
    
    # Find student directly using fuzzy matching
    students = fetch_students(db, owner_user_id=owner_user_id)
    matched_student = None
    best_score = 0
    
    # Try to find student by matching against full database names
    query_upper = query.upper()
    for student in students:
        # Check if full name appears in query (case-insensitive, exact)
        if student.name.upper() in query_upper:
            matched_student = student
            break
        # Check for USN pattern
        if student.usn.upper() in query_upper:
            matched_student = student
            break
    
    if not matched_student:
        # Try fuzzy name matching - split query into tokens and match against name parts
        query_tokens = set(normalized_query.split())
        for student in students:
            name_tokens = set(_normalize_text(student.name).split())
            # Score based on how many name tokens appear in query
            score = len(name_tokens & query_tokens) * 2
            # Bonus for longer matched tokens
            for token in (name_tokens & query_tokens):
                if len(token) >= 4:
                    score += 1
            if score > best_score:
                best_score = score
                matched_student = student
    
    if not matched_student or best_score == 0:
        return None
    
    # Find all subjects where student got the requested grade
    grade_upper = str(grade).upper()
    matching_results = []
    
    for result in _student_results(matched_student):
        if str(result.grade).upper() == grade_upper:
            matching_results.append(result)
    
    if not matching_results:
        return _student_response(
            "GET_SUBJECTS_WITH_GRADE",
            f"Student {matched_student.name} ({matched_student.usn}) does not have any '{grade_upper}' grades in the dataset.",
            [matched_student],
            meta={"query_type": "lookup", "confidence": 0.9, "grade": grade_upper},
        )
    
    # Format answer based on number of matches
    if len(matching_results) == 1:
        answer = f"Student {matched_student.name} ({matched_student.usn}) got grade '{grade_upper}' in: {matching_results[0].subject}."
    else:
        subjects = ", ".join(sorted(set(result.subject for result in matching_results)))
        answer = f"Student {matched_student.name} ({matched_student.usn}) got grade '{grade_upper}' in: {subjects}."
    
    return _student_response(
        "GET_SUBJECTS_WITH_GRADE",
        answer,
        [matched_student],
        meta={
            "query_type": "lookup",
            "confidence": 1.0,
            "grade": grade_upper,
            "matched_subjects": [result.subject for result in matching_results],
            "count": len(matching_results),
        },
    )


def _execute_subject_result_query(db: Session, query: str, history: Optional[Sequence[Dict[str, object]]] = None, owner_user_id: Optional[int] = None) -> Optional[Dict[str, object]]:
    normalized_query = _normalize_text(query)
    asks_grade = "grade" in normalized_query
    asks_gp = "gp" in normalized_query or "grade point" in normalized_query
    asks_sgpa = "sgpa" in normalized_query
    subject_phrase = _extract_subject_phrase(query)
    asks_all_subjects = (
        "all subjects" in normalized_query
        or "all grades" in normalized_query
        or "subject list" in normalized_query
        or ("list" in normalized_query and "subject" in normalized_query)
    )
    if subject_phrase and _is_generic_subject_phrase(subject_phrase) and not asks_all_subjects:
        return None
    asks_subject = bool(subject_phrase)
    if not (((asks_grade or asks_gp or asks_sgpa) and asks_subject) or asks_all_subjects):
        return None
    if _is_multi_student_request(query):
        return None
    if not _has_student_reference(query):
        return _empty_response(
            "Please include a student name or USN for subject-level questions.",
            meta={"query_type": "lookup"},
        )

    detail_meta = {"query_type": "lookup", "include_details": False}

    matched_students = _find_students_from_query_or_history(db, query, history, owner_user_id=owner_user_id)
    if not matched_students:
        return None

    if len(matched_students) > 1:
        matched_students = matched_students[:1]

    student = matched_students[0]
    if (subject_phrase and _is_generic_subject_phrase(subject_phrase)) or asks_all_subjects:
        result_bits = []
        for result in _student_results(student):
            details = [f"grade {_result_grade(result).upper()}"]
            if asks_gp and _result_gp(result) is not None:
                details.append(f"GP {float(_result_gp(result) or 0.0):.2f}")
            result_bits.append(f"{_result_subject(result)}: {', '.join(details)}")
        return _student_response(
            "GET_ALL_SUBJECT_RESULTS",
            f"All subject results for {student.name} ({student.usn}): " + "; ".join(result_bits) + ".",
            [student],
            meta={**detail_meta, "confidence": 1.0},
        )

    matched_results = _find_subject_results(student, query)
    if not matched_results:
        if subject_phrase or _build_subject_query_tokens(query, student=student):
            return _student_response(
                "GET_SUBJECT_RESULT",
                f"I could not find a subject matching '{subject_phrase or 'that request'}' for {student.name} in the uploaded results.",
                [student],
                meta={**detail_meta, "subject": subject_phrase, "confidence": 0.9},
            )
        return None
    unique_results = []
    seen_results = set()
    for result in matched_results:
        result_key = (_result_subject(result), _result_grade(result).upper(), _result_gp(result))
        if result_key in seen_results:
            continue
        seen_results.add(result_key)
        unique_results.append(result)
    matched_results = unique_results

    if len(matched_results) == 1:
        answer_bits = [f"For {student.name}, in {matched_results[0].subject}"]
        if asks_grade:
            answer_bits.append(f"the grade is {str(matched_results[0].grade).upper()}")
        if asks_gp and matched_results[0].gp is not None:
            answer_bits.append(f"the grade point is {float(matched_results[0].gp):.2f}")
        elif asks_gp:
            answer_bits.append("the grade point is not available")
        if asks_sgpa:
            answer_bits.append(f"the overall SGPA is {float(student.sgpa):.2f}")
        answer_text = ", and ".join(answer_bits) + "."
    else:
        result_bits = []
        for result in matched_results:
            bit = f"{result.subject}:"
            details = []
            if asks_grade:
                details.append(f"grade {str(result.grade).upper()}")
            if asks_gp:
                if result.gp is not None:
                    details.append(f"grade point {float(result.gp):.2f}")
                else:
                    details.append("grade point not available")
            result_bits.append(f"{bit} {', '.join(details)}")
        answer_text = f"For {student.name}, I found multiple matching subjects. " + "; ".join(result_bits) + "."
        if asks_sgpa:
            answer_text += f" The overall SGPA is {float(student.sgpa):.2f}."

    return _student_response(
        "GET_SUBJECT_RESULT",
        answer_text,
        [student],
        meta={**detail_meta, "subject": matched_results[0].subject, "matched_subjects": [result.subject for result in matched_results], "grade": str(matched_results[0].grade).upper(), "gp": float(matched_results[0].gp) if matched_results[0].gp is not None else None, "confidence": 1.0},
    )


def _execute_cross_subject_comparison_query(db: Session, query: str, owner_user_id: Optional[int] = None) -> Optional[Dict[str, object]]:
    subject_contrast = _extract_contrast_subject_phrases(query)
    if not subject_contrast:
        return None

    stronger_subject, weaker_subject = subject_contrast
    students = fetch_students(db, owner_user_id=owner_user_id)
    comparison_rows = []
    for student in students:
        stronger_match = _best_subject_match(student, stronger_subject)
        weaker_match = _best_subject_match(student, weaker_subject)
        if not stronger_match or not weaker_match:
            continue
        stronger_gp = _result_gp(stronger_match)
        weaker_gp = _result_gp(weaker_match)
        if stronger_gp is None or weaker_gp is None:
            continue
        gap = float(stronger_gp) - float(weaker_gp)
        if gap >= 10:
            comparison_rows.append((gap, student, stronger_match, weaker_match))

    if not comparison_rows:
        return _empty_response(
            f"I could not find clear student comparisons for '{stronger_subject}' versus '{weaker_subject}' in the current dataset.",
            meta={"query_type": "contextual"},
        )

    comparison_rows.sort(key=lambda item: (item[0], float(item[1].sgpa)), reverse=True)
    preview = comparison_rows[:5]
    answer = "; ".join(
        f"{student.name}: {_result_subject(stronger_match)} GP {float(_result_gp(stronger_match) or 0.0):.2f} vs {_result_subject(weaker_match)} GP {float(_result_gp(weaker_match) or 0.0):.2f}"
        for _, student, stronger_match, weaker_match in preview
    )
    return _student_response(
        "CONTEXTUAL_ANSWER",
        f"Students who appear stronger in {stronger_subject} but weaker in {weaker_subject} include {answer}.",
        [student for _, student, _, _ in preview],
        meta={"query_type": "contextual", "confidence": 0.75, "comparison": {"stronger_subject": stronger_subject, "weaker_subject": weaker_subject}},
    )


def _student_query_score(query: str, student: object) -> int:
    lowered_query = query.lower()
    name = student.name.lower()
    usn = student.usn.lower()
    score = 0

    if usn in lowered_query or lowered_query in usn:
        score += 10
    if name in lowered_query or lowered_query in name:
        score += 10

    for token in [item for item in lowered_query.split() if len(item) > 1]:
        if token in name:
            score += 2
        if token in usn:
            score += 3
        if any(token in (result.subject or "").lower() for result in student.results):
            score += 1
        if any(token == (result.grade or "").lower() for result in student.results):
            score += 2
    return score


def _serialize_result_row(student: object, result: object) -> Dict[str, object]:
    return {
        "usn": _student_usn(student),
        "name": _student_name(student),
        "sgpa": _student_sgpa(student),
        "subject": _result_subject(result),
        "grade": _result_grade(result).upper(),
        "gp": _result_gp(result),
        "pass_fail": "FAIL" if any((_result_grade(item) or "").upper() == "F" for item in _student_results(student)) else "PASS",
    }


def _subject_statistics(students: Sequence[object]) -> List[Dict[str, object]]:
    subject_map: Dict[str, Dict[str, object]] = {}
    for student in students:
        for result in student.results:
            key = result.subject
            entry = subject_map.setdefault(
                key,
                {
                    "subject": key,
                    "grades": [],
                    "gps": [],
                    "student_count": 0,
                    "fail_count": 0,
                },
            )
            entry["student_count"] = int(entry["student_count"]) + 1
            grade = str(result.grade or "NA").upper()
            entry["grades"].append(grade)
            if grade == "F":
                entry["fail_count"] = int(entry["fail_count"]) + 1
            if result.gp is not None:
                entry["gps"].append(float(result.gp))

    stats: List[Dict[str, object]] = []
    for entry in subject_map.values():
        gps = entry.pop("gps")
        grades = entry.pop("grades")
        distribution: Dict[str, int] = {}
        for grade in grades:
            distribution[grade] = distribution.get(grade, 0) + 1
        student_count = int(entry["student_count"])
        fail_count = int(entry["fail_count"])
        average_gp = round(sum(gps) / len(gps), 2) if gps else 0.0
        stats.append(
            {
                "subject": entry["subject"],
                "student_count": student_count,
                "fail_count": fail_count,
                "fail_rate": round(fail_count / student_count, 3) if student_count else 0.0,
                "average_gp": average_gp,
                "grade_distribution": distribution,
            }
        )
    return stats


def _challenging_subjects(subject_stats: Sequence[Dict[str, object]], limit: int = 5) -> List[Dict[str, object]]:
    ranked = sorted(
        subject_stats,
        key=lambda item: (float(item.get("fail_rate", 0.0)), -float(item.get("average_gp", 0.0))),
        reverse=True,
    )
    return list(ranked[:limit])


def _result_query_score(query: str, student: object, result: object) -> int:
    tokens = _query_words(query)
    if not tokens:
        return 0

    student_name = _normalize_text(_student_name(student))
    student_usn = _student_usn(student).lower()
    subject_text = _normalize_text(_result_subject(result) or "")
    grade_text = _result_grade(result).lower()
    score = 0

    for token in tokens:
        if token in subject_text:
            score += 4
        if token in student_name:
            score += 3
        if token in student_usn:
            score += 4
        if token == grade_text:
            score += 2
        if token == "sgpa":
            score += 1

    subject_phrase = _extract_subject_phrase(query)
    if subject_phrase:
        normalized_subject_phrase = _normalize_text(subject_phrase)
        if normalized_subject_phrase and normalized_subject_phrase in subject_text:
            score += 10

    return score


def _top_result_rows_for_query(
    students: Sequence[object],
    query: str,
    *,
    limit: int = 12,
) -> List[Dict[str, object]]:
    ranked_rows = []
    for student in students:
        for result in _student_results(student):
            score = _result_query_score(query, student, result)
            if score > 0:
                ranked_rows.append((score, _student_sgpa(student), _serialize_result_row(student, result)))

    ranked_rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    seen_keys = set()
    selected_rows: List[Dict[str, object]] = []
    for _, _, row in ranked_rows:
        key = (row["usn"], row["subject"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        selected_rows.append(row)
        if len(selected_rows) >= limit:
            break
    return selected_rows


def _best_subject_match(student: object, subject_phrase: str) -> Optional[object]:
    if not subject_phrase.strip():
        return None
    matches = _find_subject_results(student, f"subject {subject_phrase}",)
    return matches[0] if matches else None


def _extract_contrast_subject_phrases(query: str) -> Optional[Tuple[str, str]]:
    lowered = query.lower().strip()
    patterns = [
        r"(?:strong|good|better|best)\s+in\s+([a-z][a-z0-9\s&\-\+]+?)\s+but\s+(?:weak|worse|poor)\s+in\s+([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        r"high\s+in\s+([a-z][a-z0-9\s&\-\+]+?)\s+but\s+low\s+in\s+([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            left = re.sub(r"\s+", " ", match.group(1)).strip()
            right = re.sub(r"\s+", " ", match.group(2)).strip()
            if left and right:
                return left, right
    return None


def _is_failed_in_subject_query(query: str) -> bool:
    """Check if query is asking for students who failed in a specific subject."""
    lowered = query.lower()
    if "failed in another subject" in lowered or "failed another subject" in lowered:
        return False
    
    # Failure markers with various phrasings
    failure_markers = [
        # Direct "failed in" patterns
        "failed in",
        "failed in the",
        "who failed in",
        "students failed in",
        "list.*failed in",
        "find.*failed in",
        # "F" grade patterns
        "got f in",
        "with f in",
        "got f grade",
        "f grade in",
        "students with f in",
        # "GP 0" patterns
        "gp 0 in",
        "gp zero in",
        "gp=0 in",
        "zero gp in",
        "students with gp 0",
        # Natural language variations
        "didn't pass",
        "did not pass",
        "didn't clear",
        "did not clear",
        "flunked",
        "messed up",
        "got back",
        # "any subject" patterns
        "any subject",
        "all subjects",
        "each subject",
    ]
    return any(marker in lowered for marker in failure_markers)


def _extract_failure_subject(query: str) -> Optional[str]:
    """Extract the subject from a failure query.
    
    Handles patterns like:
    - "failed in [subject]"
    - "students with F in [subject]"
    - "GP 0 in [subject]"
    - "who didn't pass [subject]"
    - "any subject" → returns None (handled by caller)
    """
    lowered = query.lower().strip()
    lowered = re.sub(r"\s+\d+:\d+:\d+:\d+\s*$", "", lowered)
    
    # Check for "any/all/each subject" patterns first
    if re.search(r"\b(any|all|each)\s+subject", lowered):
        return None  # Signal to use general filter
    
    # Comprehensive patterns for subject extraction
    patterns = [
        # "failed in [subject]" and variants
        r"(?:failed|fail)\s+in\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)(?:\s+(?:subject|course|paper|exam|class|lab))?[\?\.]?$",
        # "F in [subject]" / "with F in [subject]"
        r"(?:with\s+)?f\s+(?:grade\s+)?in\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        r"(?:got\s+)?f\s+in\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        r"f\s+grade\s+in\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        # "GP 0 in [subject]"
        r"gp\s+(?:=\s*)?0\s+in\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        r"(?:zero\s+)?gp\s+in\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        # "who/students/list with [condition] in [subject]"
        r"(?:who\s+|students\s+|list\s+)?(?:students\s+)?(?:with\s+)?(?:f|gp\s+0)\s+(?:in|for)\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        # "didn't pass/clear [subject]"
        r"(?:didn't|did\s+not)\s+(?:pass|clear)\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        # "flunked/messed up/got back in [subject]"
        r"(?:flunked|messed\s+up|got\s+back)\s+(?:in\s+)?(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        # "in [subject]" (for queries like "in chemistry lab")
        r"\bin\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)(?:\s+subject)?[\?\.]?$",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            subject = re.sub(r"\s+", " ", match.group(1)).strip()
            # Remove trailing keywords that are not part of subject name
            subject = re.sub(r"\s+(?:subject|course|paper|exam|class|lab|list|results?)$", "", subject)
            if not subject or subject in {"another", "other", "any", "all", "every", "one", "some", "that", "this"}:
                return None
            if subject in {"another subject", "other subject"}:
                return None
            if subject:
                return subject
    return None


def _is_passing_in_subject_query(query: str) -> bool:
    """Check if query is asking for students who PASSED in a specific subject."""
    lowered = query.lower()
    
    pass_markers = [
        "passed in",
        "passed in the",
        "who passed in",
        "students passed in",
        "didn't fail",
        "did not fail",
        "didn't get f",
        "no f in",
        "no failures in",
        "with a in",
        "got a in",
        "with a+ in",
        "got a+ in",
        "highest in",
        "scored highest in",
    ]
    return any(marker in lowered for marker in pass_markers)


def _extract_passing_subject(query: str) -> Optional[str]:
    """Extract subject from a 'passed in [subject]' query."""
    lowered = query.lower().strip()
    lowered = re.sub(r"\s+\d+:\d+:\d+:\d+\s*$", "", lowered)
    
    patterns = [
        r"passed?\s+in\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        r"(?:with\s+)?a\+?\s+(?:in|for)\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        r"(?:got|scored)\s+(?:highest|best)\s+in\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        r"no\s+(?:f|failures)\s+in\s+(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
        r"didn't\s+(?:fail|get\s+f)\s+(?:in\s+)?(?:the\s+)?([a-z][a-z0-9\s&\-\+]+?)[\?\.]?$",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            subject = re.sub(r"\s+", " ", match.group(1)).strip()
            subject = re.sub(r"\s+(?:subject|course|paper|exam|class|lab)$", "", subject)
            if subject:
                return subject
    return None


def _is_sgpa_range_query(query: str) -> bool:
    """Check if query is asking for students by SGPA range."""
    lowered = query.lower()
    markers = [
        "sgpa above",
        "sgpa more than",
        "sgpa greater than",
        "sgpa over",
        "sgpa at least",
        "sgpa below",
        "sgpa between",
        "sgpa greater than",
        "sgpa less than",
        "sgpa from",
        "sgpa to",
        "sgpa above",
        "sgpa below",
        "more than",
        "greater than",
        "over",
        "at least",
        "less than",
        "below",
    ]
    # Accept queries that mention either SGPA or CGPA
    return any(marker in lowered for marker in markers) or "cgpa" in lowered


def _extract_sgpa_range(query: str) -> Optional[dict]:
    """Extract SGPA range from query.
    
    Returns dict with min_sgpa and max_sgpa, or None if not found.
    """
    lowered = query.lower()
    
    # Pattern: "sgpa/cgpa above X"
    match = re.search(r"(?:(?:sgpa|cgpa)\s+(?:above|greater than|more than|over|at least)|(?:more than|greater than|over|at least)\s+(?:sgpa|cgpa)(?:\s+of)?|(?:sgpa|cgpa)\s*>=|(?:sgpa|cgpa)\s*>)\s*([\d.]+)", lowered)
    if not match:
        match = re.search(r"(?:above|greater than|more than|over|at least)\s*([\d.]+)\s*(?:sgpa|cgpa|gpa)?", lowered)
    if match:
        return {"min_sgpa": float(match.group(1)), "max_sgpa": 10.0}
    
    # Pattern: "sgpa/cgpa below X"
    match = re.search(r"(?:(?:sgpa|cgpa)\s+(?:below|less than|under)|(?:less than|below|under)\s+(?:sgpa|cgpa)(?:\s+of)?|(?:sgpa|cgpa)\s*<=|(?:sgpa|cgpa)\s*<)\s*([\d.]+)", lowered)
    if not match:
        match = re.search(r"(?:below|less than|under|at most)\s*([\d.]+)\s*(?:sgpa|cgpa|gpa)?", lowered)
    if match:
        return {"min_sgpa": 0.0, "max_sgpa": float(match.group(1))}
    
    # Pattern: "sgpa/cgpa between X and Y"
    match = re.search(r"(?:(?:sgpa|cgpa)\s+)?(?:between|from)\s+([\d.]+)\s+(?:and|to)\s+([\d.]+)", lowered)
    if match:
        min_val = float(match.group(1))
        max_val = float(match.group(2))
        return {"min_sgpa": min(min_val, max_val), "max_sgpa": max(min_val, max_val)}
    
    # Pattern: "sgpa/cgpa from X to Y"
    match = re.search(r"(?:sgpa|cgpa)\s+from\s+([\d.]+)\s+to\s+([\d.]+)", lowered)
    if match:
        min_val = float(match.group(1))
        max_val = float(match.group(2))
        return {"min_sgpa": min(min_val, max_val), "max_sgpa": max(min_val, max_val)}
    
    return None


def _is_multi_intent_query(query: str) -> bool:
    """Check if query contains multiple intents joined by 'and'."""
    lowered = query.lower()
    # Look for " and " pattern that separates different queries
    and_patterns = [
        r"\band\b",  # word boundary "and"
    ]
    count = 0
    for pattern in and_patterns:
        count += len(re.findall(pattern, lowered))
    return count >= 1


def _split_multi_intent_query(query: str) -> Optional[List[str]]:
    """Split multi-intent query by 'and' conjunctions."""
    # Use regex to split by " and " (case-insensitive)
    parts = re.split(r'\s+and\s+', query.lower())
    
    if len(parts) < 2:
        return None
    
    # Clean and validate parts
    queries = []
    for part in parts:
        part = part.strip()
        if len(part) > 3:  # Ignore very short fragments
            queries.append(part)
    
    return queries if len(queries) >= 2 else None


def _similarity_score(a: str, b: str) -> float:
    """Calculate similarity between two strings (0-1)."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _correct_typo(word: str, keywords: List[str], threshold: float = 0.75) -> Optional[str]:
    """Find best matching keyword for potentially misspelled word."""
    best_match = None
    best_score = 0
    
    for keyword in keywords:
        score = _similarity_score(word, keyword)
        if score > threshold and score > best_score:
            best_match = keyword
            best_score = score
    
    return best_match


def _build_query_context(
    db: Session,
    query: str,
    history: Optional[Sequence[Dict[str, object]]] = None,
    retrieval_mode: str = "hybrid",
    owner_user_id: Optional[int] = None,
) -> Dict[str, object]:
    students = fetch_students(db, owner_user_id=owner_user_id)
    scored_students = sorted(
        students,
        key=lambda student: (_student_query_score(query, student), float(student.sgpa)),
        reverse=True,
    )
    selected_students = [student for student in scored_students if _student_query_score(query, student) > 0][:8]

    recent_usns: List[str] = []
    for item in reversed(list(history or [])):
        for usn in item.get("student_usns", []) if isinstance(item, dict) else []:
            normalized = str(usn).upper()
            if normalized and normalized not in recent_usns:
                recent_usns.append(normalized)
        if len(recent_usns) >= 4:
            break
    for student in fetch_students_by_usns(db, recent_usns, owner_user_id=owner_user_id):
        if all(existing.usn != student.usn for existing in selected_students):
            selected_students.insert(0, student)

    retrieved_chunks: List[Dict[str, object]] = []
    top_result_rows: List[Dict[str, object]] = []

    if retrieval_mode in {"semantic", "hybrid"}:
        try:
            semantic_hits = QueryIntelligenceIndex(owner_user_id=owner_user_id).search(query, top_k=12)
            semantic_usns = [
                str(hit["metadata"].get("usn")).upper()
                for hit in semantic_hits
                if str(hit["metadata"].get("type", "")).startswith("student") and hit["metadata"].get("usn")
            ]
            for student in fetch_students_by_usns(db, semantic_usns, owner_user_id=owner_user_id):
                if all(existing.usn != student.usn for existing in selected_students):
                    selected_students.append(student)
                if len(selected_students) >= 8:
                    break
        except Exception:
            pass
        retrieved_chunks = retrieve_context_documents(query, top_k=10, owner_user_id=owner_user_id)

    if retrieval_mode in {"sql", "hybrid"}:
        top_result_rows = _top_result_rows_for_query(students, query, limit=20)

    summary_students = fetch_students(db, owner_user_id=owner_user_id)
    topper = fetch_topper(db, owner_user_id=owner_user_id)
    subject_stats = _subject_statistics(summary_students)
    summary = {
        "total_students": len(summary_students),
        "average_sgpa": compute_average_sgpa(db),
        "average_gp": compute_average_gp(summary_students),
        "topper": serialize_student(topper) if topper else None,
        "failed_count": sum(
            1 for student in summary_students if any((result.grade or "").upper() == "F" for result in student.results)
        ),
        "grade_distribution": compute_grade_distribution(summary_students),
        "challenging_subjects": _challenging_subjects(subject_stats, limit=5),
    }
    return {
        "schema": {
            "students": ["usn", "name", "sgpa", "pass_fail"],
            "results": ["subject", "grade", "gp"],
        },
        "summary": summary,
        # Keep context compact for LLM grounding while still DB-backed.
        "students": [
            {"usn": student.usn, "name": student.name, "sgpa": float(student.sgpa), "pass_fail": "FAIL" if any((r.grade or "").upper() == "F" for r in (student.results or [])) else "PASS"}
            for student in selected_students
        ],
        # Always include top matching result rows so answers can cite DB-backed evidence.
        "matching_results": top_result_rows,
        "subject_statistics": subject_stats[:20],
        "conversation_focus": {
            "recent_student_usns": recent_usns[:4],
            "subject_hint": _extract_subject_phrase(query),
            "retrieval_mode": retrieval_mode,
        },
        "retrieved_chunks": [
            {
                "content": str(item.get("page_content", "")),
                "metadata": item.get("metadata", {}),
                "score": float(item.get("score", 0.0)),
            }
            for item in retrieved_chunks
        ],
    }


def _lookup_students_by_name_local(db: Session, student_name: str, owner_user_id: Optional[int] = None) -> List[object]:
    normalized = " ".join(student_name.lower().split())
    students = fetch_students(db, owner_user_id=owner_user_id)

    exact_matches = [student for student in students if " ".join(student.name.lower().split()) == normalized]
    if exact_matches:
        return exact_matches

    # Try matching with spaces removed (e.g., "meenakumari" vs "meena kumari")
    compact_query = "".join(normalized.split())
    compact_matches = [
        student for student in students
        if "".join(student.name.lower().split()) == compact_query
    ]
    if compact_matches:
        return compact_matches

    whole_phrase_matches = [student for student in students if normalized in " ".join(student.name.lower().split())]
    if whole_phrase_matches:
        return whole_phrase_matches

    tokens = [token for token in normalized.split() if token]
    if len(tokens) >= 2:
        token_matches = [
            student
            for student in students
            if all(token in student.name.lower() for token in tokens)
        ]
        if token_matches:
            return token_matches

        scored_matches = []
        query_tokens = [token for token in tokens if token]
        for student in students:
            name_tokens = _normalize_text(student.name).split()
            score = 0.0
            for token in query_tokens:
                if any(token == name_token for name_token in name_tokens if len(name_token) > 1):
                    score += 2.0
                elif any((token in name_token or name_token in token) for name_token in name_tokens if len(name_token) > 1 and len(token) >= 4):
                    score += 1.2
                elif any(token in name_token for name_token in name_tokens if len(name_token) > 1 and len(token) == 3):
                    score += 0.5
                elif len(token) == 1 and name_tokens and token == name_tokens[0][0]:
                    score += 0.75
            ratio = SequenceMatcher(None, normalized, _normalize_text(student.name)).ratio()
            if score >= 1.75 or ratio >= 0.62:
                scored_matches.append((score, ratio, float(student.sgpa), student))
        if scored_matches:
            scored_matches.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
            best_score = scored_matches[0][0]
            best_ratio = scored_matches[0][1]
            best_matches = [
                student
                for score, ratio, _, student in scored_matches
                if abs(score - best_score) < 0.001 and abs(ratio - best_ratio) < 0.001
            ]
            if len(best_matches) == 1:
                return best_matches

    return []


def _answer_for_single_student_query(query: str, student: object) -> str:
    lowered = query.lower()
    grades = [str(result.grade).upper() for result in student.results]
    unique_grades = ", ".join(grades) if grades else "no recorded grades"
    failed_subjects = [result for result in student.results if str(result.grade or "").upper() == "F"]
    overall_status = "FAIL" if failed_subjects else "PASS"

    if "pass all subjects" in lowered or "passed all subjects" in lowered:
        if overall_status == "PASS":
            return f"Yes. {student.name} ({student.usn}) passed all subjects."
        return (
            f"No. {student.name} ({student.usn}) did not pass all subjects "
            f"and has {len(failed_subjects)} failing subject(s)."
        )

    if ("pass" in lowered and "fail" in lowered) or "pass or fail" in lowered:
        return f"{student.name} ({student.usn}) is {overall_status}."

    if "how did i perform" in lowered or "how did" in lowered and "perform" in lowered:
        if overall_status == "PASS":
            return f"{student.name} ({student.usn}) performed well with SGPA {float(student.sgpa):.2f} and overall PASS status."
        return (
            f"{student.name} ({student.usn}) has SGPA {float(student.sgpa):.2f} and overall FAIL status "
            f"with {len(failed_subjects)} failing subject(s)."
        )

    if "sgpa" in lowered:
        return f"{student.name} has SGPA {float(student.sgpa):.2f}."
    if "gp" in lowered or "grade point" in lowered:
        gp_bits = [
            f"{_result_subject(result)}: GP {float(_result_gp(result) or 0.0):.2f}"
            for result in _student_results(student)
            if _result_gp(result) is not None
        ]
        return f"{student.name} ({student.usn}) grade points: " + "; ".join(gp_bits) + "."
    if "grade" in lowered:
        return f"{student.name} received these grades: {unique_grades}."
    if "details" in lowered or "result" in lowered or "show" in lowered:
        return f"Showing full result details for {student.name} ({student.usn})."
    return f"Found the student record for {student.name} ({student.usn})."


def _total_gp(student: object) -> float:
    return sum(float(_result_gp(result) or 0.0) for result in _student_results(student))


def _average_gp_for_student(student: object) -> float:
    values = [float(_result_gp(result)) for result in _student_results(student) if _result_gp(result) is not None]
    return sum(values) / len(values) if values else 0.0


def _student_summary_dict(student: object) -> Dict[str, object]:
    return {
        "usn": _student_usn(student),
        "name": _student_name(student),
        "sgpa": float(_student_sgpa(student)),
        "pass_fail": "FAIL" if any(_result_grade(result).upper() == "F" for result in _student_results(student)) else "PASS",
    }


def _match_subject_from_results(results_df: pd.DataFrame, subject_query: Optional[str]) -> Optional[str]:
    if not subject_query or results_df.empty or "subject" not in results_df.columns:
        return None

    available_subjects = sorted({str(subject).strip() for subject in results_df["subject"].dropna() if str(subject).strip()})
    normalized_subject_query = _normalize_text(subject_query)
    if not normalized_subject_query:
        return None
    
    # Check if the subject_query is a generic phrase that shouldn't match specific subjects
    generic_phrases = {"a subject", "any subject", "all subjects", "every subject", "each subject", "the subject", "one subject", "another subject", "in subject"}
    if normalized_subject_query in generic_phrases:
        return None

    matched_subject = None
    best_match_score = 0.0
    query_tokens = {token for token in normalized_subject_query.split() if len(token) > 1 and token not in SUBJECT_QUERY_STOPWORDS}
    for available_subject in available_subjects:
        normalized_available = _normalize_text(available_subject)
        subject_tokens = {token for token in normalized_available.split() if len(token) > 1}
        if normalized_available == normalized_subject_query:
            return available_subject
        if normalized_subject_query in normalized_available:
            score = len(normalized_subject_query) * 10 / max(len(normalized_available), 1)
            if score > best_match_score:
                matched_subject = available_subject
                best_match_score = score
        elif normalized_available in normalized_subject_query:
            score = len(normalized_available) * 5
            if score > best_match_score:
                matched_subject = available_subject
                best_match_score = score
        if query_tokens and subject_tokens:
            overlap = query_tokens & subject_tokens
            if overlap:
                score = (len(overlap) / len(subject_tokens)) + (len(overlap) / len(query_tokens))
                code = normalized_available.split()[0] if normalized_available.split() else ""
                if code and code in query_tokens:
                    score += 1.0
                if score > best_match_score:
                    matched_subject = available_subject
                    best_match_score = score
    return matched_subject


def _database_query_shape(query: str) -> Dict[str, bool]:
    normalized = _normalize_text(query)
    return {
        "count": any(token in normalized for token in ["count", "how many", "number of", "total"]),
        "list": any(token in normalized for token in ["list", "show", "display", "find", "who", "which students"]),
        "records": "record" in normalized or "records" in normalized,
        "average": "average" in normalized or "mean" in normalized,
        "highest": any(token in normalized for token in ["highest", "maximum", "max", "best"]),
        "lowest": any(token in normalized for token in ["lowest", "minimum", "min", "worst"]),
        "distribution": "distribution" in normalized or "breakdown" in normalized,
        "percentage": "percentage" in normalized or "percent" in normalized,
        "rank": "rank" in normalized or "sorted" in normalized or "top" in normalized,
    }


def _extract_database_entities(query: str, students: Sequence[object], results_df: pd.DataFrame) -> Dict[str, object]:
    normalized = _normalize_text(query)
    grade_value = _extract_grade_value(query)
    if not grade_value and not results_df.empty and "grade" in results_df.columns:
        available_grades = sorted({str(grade).upper() for grade in results_df["grade"].dropna() if str(grade).strip()}, key=len, reverse=True)
        normalized_with_symbols = query.upper().replace("PLUS", "+")
        for grade in available_grades:
            if grade and re.search(rf"(?<![A-Z0-9+]){re.escape(grade)}(?![A-Z0-9+])", normalized_with_symbols):
                grade_value = grade
                break

    subject_candidate = _extract_subject_phrase_intel(query) or _extract_subject_phrase(query)
    matched_subject = _match_subject_from_results(results_df, subject_candidate)
    if not matched_subject:
        matched_subject = _match_subject_from_results(results_df, query)
    usn_value = _extract_usn_value(query)
    name_value = _extract_name_value(query)
    shape = _database_query_shape(query)
    explicit_student_reference = bool(usn_value or (name_value and not any(shape.values())))

    return {
        "normalized": normalized,
        "shape": shape,
        "grade": grade_value,
        "subject": matched_subject,
        "raw_subject": subject_candidate,
        "usn": usn_value,
        "name": name_value,
        "has_student_reference": explicit_student_reference,
        "mentions_students": "student" in normalized or "students" in normalized or "usn" in normalized,
        "mentions_grade": "grade" in normalized or bool(grade_value),
        "mentions_gp": "gp" in normalized or "grade point" in normalized,
        "mentions_sgpa": ("sgpa" in normalized) or ("cgpa" in normalized),
        "mentions_subject": "subject" in normalized or bool(matched_subject) or bool(subject_candidate),
        "mentions_fail": "fail" in normalized or grade_value == "F",
        "mentions_pass": "pass" in normalized and "fail" not in normalized,
    }


def _format_student_filter_response(
    intent: str,
    answer: str,
    students: Sequence[object],
    meta: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    return {
        "intent": intent,
        "answer": answer,
        "students": [_student_summary_dict(student) for student in students],
        "meta": meta or {},
        "suggestions": [],
    }


def _execute_entity_driven_database_query(
    students: Sequence[object],
    results_df: pd.DataFrame,
    query: str,
) -> Optional[Dict[str, object]]:
    if not students:
        return None

    entities = _extract_database_entities(query, students, results_df)
    shape = entities["shape"]
    normalized = str(entities["normalized"])
    grade_value = entities.get("grade")
    subject = entities.get("subject")
    mentions_students = bool(entities.get("mentions_students"))
    has_student_reference = bool(entities.get("has_student_reference"))

    if has_student_reference:
        return None

    if results_df.empty:
        return None

    filtered_df = results_df
    if subject:
        filtered_df = filtered_df[filtered_df["subject"] == subject]
    if grade_value:
        filtered_df = filtered_df[filtered_df["grade"] == grade_value]
    elif entities.get("mentions_fail") and subject:
        filtered_df = filtered_df[filtered_df["grade"] == "F"]
    elif entities.get("mentions_pass") and subject:
        filtered_df = filtered_df[filtered_df["grade"] != "F"]

    has_db_entity = bool(subject or grade_value or entities.get("mentions_gp") or entities.get("mentions_sgpa") or entities.get("mentions_fail") or entities.get("mentions_pass"))
    has_db_operation = any(bool(value) for value in shape.values()) or mentions_students or entities.get("mentions_grade")
    if not (has_db_entity and has_db_operation):
        return None
    if ("per subject" in normalized or "by subject" in normalized) and not subject:
        return None

    subject_text = f" in {subject}" if subject else ""
    grade_text = f" grade {grade_value}" if grade_value else ""
    record_count = int(len(filtered_df)) if not filtered_df.empty else 0
    matched_usns = list(dict.fromkeys(filtered_df["usn"].astype(str).tolist())) if not filtered_df.empty else []
    student_map = {_student_usn(student): student for student in students}
    matched_students = [student_map[usn] for usn in matched_usns if usn in student_map]

    # Special-case: if the query mentions SGPA ranges (e.g., "SGPA less than 7"),
    # apply the SGPA filter over the student records instead of the subject-level
    # results dataframe. The earlier filtered_df operates on subject rows and
    # will return all students when no subject/grade filters exist which causes
    # SGPA queries to unintentionally return the whole dataset.
    if entities.get("mentions_sgpa"):
        range_entities = _extract_sgpa_range(query)
        if range_entities:
            min_sgpa = float(range_entities.get("min_sgpa") or 0.0)
            max_sgpa = float(range_entities.get("max_sgpa") or 10.0)
            if min_sgpa < 0 or max_sgpa > 10 or min_sgpa > max_sgpa:
                return _empty_response(f"Invalid SGPA range: {min_sgpa}-{max_sgpa}. Valid range is 0-10.", intent="GET_SGPA_RANGE", meta={"query_type": "filter"})

            sgpa_matched = [student for student in students if min_sgpa <= float(student.sgpa or 0.0) <= max_sgpa]
            if not sgpa_matched:
                return _empty_response(
                    f"No students were found with SGPA between {min_sgpa} and {max_sgpa}.",
                    intent="GET_SGPA_RANGE",
                    meta={"query_type": "filter", "min_sgpa": min_sgpa, "max_sgpa": max_sgpa},
                )
            answer = f"Found {len(sgpa_matched)} students with SGPA between {min_sgpa} and {max_sgpa}."
            return _format_student_filter_response(
                "GET_STUDENTS_BY_DATABASE_ENTITIES",
                answer,
                sgpa_matched,
                meta={"query_type": "filter", "min_sgpa": min_sgpa, "max_sgpa": max_sgpa, "count": len(sgpa_matched), "confidence": 1.0},
            )

    if entities.get("raw_subject") and not subject:
        return _empty_response(
            f"Subject '{entities.get('raw_subject')}' was not found in the selected dataset.",
            intent="DATABASE_ENTITY_QUERY",
            meta={"query_type": "filter", "subject": entities.get("raw_subject"), "grade": grade_value, "confidence": 1.0},
        )

    if shape["distribution"] and (subject or entities.get("mentions_grade")):
        distribution = filtered_df["grade"].value_counts().sort_index().to_dict() if not filtered_df.empty else {}
        scope = f" for {subject}" if subject else ""
        answer = f"Grade distribution{scope}: " + (", ".join(f"{grade}: {count}" for grade, count in distribution.items()) or "no matching records") + "."
        return {"intent": "GET_GRADE_DISTRIBUTION", "answer": answer, "students": [], "meta": {"query_type": "aggregation", "subject": subject or "", "grade_distribution": distribution, "confidence": 1.0}, "suggestions": []}

    if shape["average"] and (entities.get("mentions_gp") or subject):
        gp_df = filtered_df.dropna(subset=["gp"])
        average_gp = float(gp_df["gp"].mean()) if not gp_df.empty else 0.0
        return {"intent": "GET_AVERAGE_GP", "answer": f"Average GP{subject_text} is {average_gp:.2f}.", "students": [], "meta": {"query_type": "aggregation", "subject": subject or "", "average_gp": round(average_gp, 2), "confidence": 1.0}, "suggestions": []}

    if shape["highest"] and entities.get("mentions_gp"):
        gp_df = filtered_df.dropna(subset=["gp"])
        if gp_df.empty:
            return _empty_response(f"No GP data is available{subject_text}.", intent="GET_HIGHEST_GP", meta={"query_type": "aggregation"})
        value = float(gp_df["gp"].max())
        return {"intent": "GET_HIGHEST_GP", "answer": f"Highest GP{subject_text} is {value:.2f}.", "students": [], "meta": {"query_type": "aggregation", "subject": subject or "", "highest_gp": value, "confidence": 1.0}, "suggestions": []}

    if shape["lowest"] and entities.get("mentions_gp"):
        gp_df = filtered_df.dropna(subset=["gp"])
        if gp_df.empty:
            return _empty_response(f"No GP data is available{subject_text}.", intent="GET_LOWEST_GP", meta={"query_type": "aggregation"})
        value = float(gp_df["gp"].min())
        return {"intent": "GET_LOWEST_GP", "answer": f"Lowest GP{subject_text} is {value:.2f}.", "students": [], "meta": {"query_type": "aggregation", "subject": subject or "", "lowest_gp": value, "confidence": 1.0}, "suggestions": []}

    if shape["percentage"]:
        total = int(results_df[results_df["subject"] == subject]["usn"].nunique()) if subject else len(students)
        count = len(matched_students)
        pct = (count / total * 100) if total else 0.0
        return {"intent": "GET_PERCENTAGE", "answer": f"{pct:.1f}% ({count}/{total}) match{grade_text}{subject_text}.", "students": [], "meta": {"query_type": "aggregation", "subject": subject or "", "grade": grade_value or "", "percentage": round(pct, 2), "count": count, "total": total, "confidence": 1.0}, "suggestions": []}

    if shape["count"] and (grade_value or subject or entities.get("mentions_fail") or entities.get("mentions_pass")):
        if shape["records"]:
            return {"intent": "COUNT_RESULT_RECORDS", "answer": f"{record_count} result record(s) match{grade_text}{subject_text}.", "students": [], "meta": {"query_type": "aggregation", "subject": subject or "", "grade": grade_value or "", "record_count": record_count, "confidence": 1.0}, "suggestions": []}
        return {"intent": "COUNT_STUDENTS", "answer": f"{len(matched_students)} students match{grade_text}{subject_text}.", "students": [], "meta": {"query_type": "aggregation", "subject": subject or "", "grade": grade_value or "", "count": len(matched_students), "record_count": record_count, "confidence": 1.0}, "suggestions": []}

    if mentions_students or shape["list"] or shape["records"]:
        if not matched_students:
            return _empty_response(
                f"No students were found matching{grade_text}{subject_text}.",
                intent="GET_STUDENTS_BY_DATABASE_ENTITIES",
                meta={"query_type": "filter", "subject": subject or "", "grade": grade_value or "", "record_count": 0, "confidence": 1.0},
            )
        answer = f"Found {len(matched_students)} students matching{grade_text}{subject_text}."
        if shape["records"]:
            answer = f"Found {record_count} result record(s) matching{grade_text}{subject_text} across {len(matched_students)} student(s)."
        return _format_student_filter_response(
            "GET_STUDENTS_BY_DATABASE_ENTITIES",
            answer,
            matched_students,
            meta={"query_type": "filter", "subject": subject or "", "grade": grade_value or "", "record_count": record_count, "confidence": 1.0},
        )

    return None


def _execute_direct_dataset_query(
    db: Session,
    query: str,
    dataset_ids: Optional[Sequence[int]] = None,
    merge: Optional[str] = None,
    owner_user_id: Optional[int] = None,
) -> Optional[Dict[str, object]]:
    normalized = _normalize_text(query)
    students = fetch_students(db, dataset_ids=dataset_ids, merge=merge, owner_user_id=owner_user_id)
    results_df = build_results_dataframe(students)
    students_df = build_students_dataframe(students)
    grade_value = _extract_grade_value(query)

    if "median sgpa" in normalized or "median of the class" in normalized or "class median" in normalized:
        sgpas = [float(student.sgpa) for student in students if student.sgpa is not None]
        median_sgpa = statistics.median(sgpas) if sgpas else 0.0
        return {"intent": "GET_MEDIAN_SGPA", "answer": f"Median SGPA is {median_sgpa:.2f}.", "students": [], "meta": {"query_type": "aggregation", "median_sgpa": round(float(median_sgpa), 2), "confidence": 1.0}, "suggestions": []}

    if "most difficult" in normalized or "hardest" in normalized or "difficult subjects" in normalized or "most challenging" in normalized:
        subject_rows = sorted(
            _subject_statistics(students),
            key=lambda item: (float(item.get("fail_rate", 0.0)), -float(item.get("average_gp", 0.0))),
            reverse=True,
        )
        preview = subject_rows[:3]
        if preview:
            answer = "The most difficult subjects are: " + "; ".join(f"{item['subject']} (fail rate {float(item.get('fail_rate', 0.0)) * 100:.1f}%, avg GP {float(item.get('average_gp', 0.0)):.2f})" for item in preview) + "."
            return {"intent": "GET_HARDEST_SUBJECTS", "answer": answer, "students": [], "meta": {"query_type": "aggregation", "subjects": preview, "confidence": 1.0}, "suggestions": []}

    if "easiest" in normalized or "easiest subject" in normalized or "best subject" in normalized or "strongest subject" in normalized:
        subject_rows = sorted(_subject_statistics(students), key=lambda item: (float(item.get("fail_rate", 0.0)), -float(item.get("average_gp", 0.0))))
        preview = subject_rows[:3]
        if preview:
            answer = "The easiest subjects are: " + "; ".join(f"{item['subject']} (fail rate {float(item.get('fail_rate', 0.0)) * 100:.1f}%, avg GP {float(item.get('average_gp', 0.0)):.2f})" for item in preview) + "."
            return {"intent": "GET_EASIEST_SUBJECTS", "answer": answer, "students": [], "meta": {"query_type": "aggregation", "subjects": preview, "confidence": 1.0}, "suggestions": []}

    if "highest average gp" in normalized or "top average gp" in normalized or "best average gp" in normalized:
        subject_rows = sorted(_subject_statistics(students), key=lambda item: float(item.get("average_gp", 0.0)), reverse=True)
        if subject_rows:
            best = subject_rows[0]
            return {"intent": "GET_BEST_SUBJECT_BY_GP", "answer": f"{best['subject']} has the highest average GP at {float(best.get('average_gp', 0.0)):.2f}.", "students": [], "meta": {"query_type": "aggregation", "subject": str(best.get('subject')), "average_gp": float(best.get('average_gp', 0.0)), "confidence": 1.0}, "suggestions": []}

    if "lowest average gp" in normalized or "worst average gp" in normalized:
        subject_rows = sorted(_subject_statistics(students), key=lambda item: float(item.get("average_gp", 0.0)))
        if subject_rows:
            worst = subject_rows[0]
            return {"intent": "GET_WORST_SUBJECT_BY_GP", "answer": f"{worst['subject']} has the lowest average GP at {float(worst.get('average_gp', 0.0)):.2f}.", "students": [], "meta": {"query_type": "aggregation", "subject": str(worst.get('subject')), "average_gp": float(worst.get('average_gp', 0.0)), "confidence": 1.0}, "suggestions": []}

    if "weak students" in normalized or "need counseling" in normalized or "needing counseling" in normalized or "at risk" in normalized:
        weak_students = [student for student in students if float(student.sgpa or 0.0) <= 5.0 or any(_result_grade(result).upper() == "F" for result in _student_results(student))]
        if weak_students:
            return _student_response(
                "GET_WEAK_STUDENTS",
                f"Found {len(weak_students)} students who may need counseling support based on SGPA <= 5.0 or at least one F grade.",
                weak_students,
                meta={"query_type": "filter", "confidence": 1.0},
            )

    if "data anomalies" in normalized or "anomalies in grade distribution" in normalized or "grade distribution anomalies" in normalized:
        distribution = compute_grade_distribution(students)
        expected_grades = {"O", "A+", "A", "B+", "B", "C", "P", "F", "NE", "X"}
        anomalous_grades = sorted([grade for grade in distribution if grade not in expected_grades])
        anomaly_text = f" Unusual grade labels detected: {', '.join(anomalous_grades)}." if anomalous_grades else " No unusual grade labels were detected."
        answer = f"Data quality check: the grade distribution spans {len(distribution)} distinct labels.{anomaly_text}"
        return {"intent": "GET_DATA_QUALITY_CHECK", "answer": answer, "students": [], "meta": {"query_type": "filter", "anomalous_grades": anomalous_grades, "confidence": 1.0}, "suggestions": []}

    entity_response = _execute_entity_driven_database_query(students, results_df, query)
    if entity_response:
        return entity_response

    if "missing grades" in normalized or "null or empty values" in normalized or "negative or invalid" in normalized or "anomalies in grading" in normalized:
        missing_rows = []
        invalid_gp_rows = []
        for student in students:
            for result in _student_results(student):
                if not str(_result_subject(result)).strip() or not str(_result_grade(result)).strip():
                    missing_rows.append((student, result))
                gp = _result_gp(result)
                if gp is not None and (float(gp) < 0 or float(gp) > 10):
                    invalid_gp_rows.append((student, result))
        distribution = compute_grade_distribution(students)
        expected_grades = {"O", "A+", "A", "B+", "B", "C", "P", "F", "NE", "X"}
        anomalous_grades = sorted([grade for grade in distribution if grade not in expected_grades])
        anomaly_text = f" Unusual grade labels detected: {', '.join(anomalous_grades)}." if anomalous_grades else " No unusual grade labels were detected."
        answer = f"Data quality check: {len(missing_rows)} records have missing subject/grade values and {len(invalid_gp_rows)} records have invalid GP values outside 0-10.{anomaly_text}"
        return {"intent": "GET_DATA_QUALITY_CHECK", "answer": answer, "students": [], "meta": {"query_type": "filter", "missing_records": len(missing_rows), "invalid_gp_records": len(invalid_gp_rows), "anomalous_grades": anomalous_grades, "confidence": 1.0}, "suggestions": []}

    if "duplicate student ids" in normalized or "duplicate student" in normalized:
        usn_counts = Counter(student.usn.upper() for student in students)
        duplicates = [usn for usn, count in usn_counts.items() if count > 1]
        return {"intent": "GET_DUPLICATE_STUDENT_IDS", "answer": f"Found {len(duplicates)} duplicate student ID(s)." + (f" Duplicates: {', '.join(duplicates)}." if duplicates else ""), "students": [], "meta": {"query_type": "filter", "duplicates": duplicates, "confidence": 1.0}, "suggestions": []}

    if "all f grades" in normalized:
        matched = [student for student in students if _student_results(student) and all(_result_grade(result).upper() == "F" for result in _student_results(student))]
        return _student_response("GET_ALL_F_STUDENTS", f"Found {len(matched)} students with all F grades.", matched, meta={"query_type": "filter", "confidence": 1.0})

    if "distribution of grades" in normalized or "grade distribution" in normalized:
        distribution = compute_grade_distribution(students)
        answer = "Grade distribution: " + ", ".join(f"{grade}: {count}" for grade, count in sorted(distribution.items())) + "."
        return {"intent": "GET_GRADE_DISTRIBUTION", "answer": answer, "students": [], "meta": {"query_type": "aggregation", "grade_distribution": distribution, "confidence": 1.0}, "suggestions": []}

    if "percentage" in normalized and "failed" in normalized:
        total = len(students)
        failed = sum(1 for student in students if any(_result_grade(result).upper() == "F" for result in _student_results(student)))
        pct = (failed / total * 100) if total else 0.0
        return {"intent": "GET_FAIL_PERCENTAGE", "answer": f"Fail percentage: {pct:.1f}% ({failed}/{total} students have at least one F grade).", "students": [], "meta": {"query_type": "aggregation", "fail_percentage": round(pct, 2), "failed_count": failed, "total_count": total, "confidence": 1.0}, "suggestions": []}

    if "how many students failed" in normalized or "count how many students failed" in normalized or "count failed students" in normalized:
        return _execute_aggregation(db, "GET_FAILED_COUNT", {}, 1.0)

    if "count" in normalized and grade_value and "student" in normalized:
        subject = _extract_subject_phrase_intel(query) or _extract_subject_phrase(query)
        matched_subject = _match_subject_from_results(results_df, subject)
        if results_df.empty:
            count = 0
        else:
            filtered_df = results_df[results_df["grade"] == grade_value]
            if matched_subject:
                filtered_df = filtered_df[filtered_df["subject"] == matched_subject]
            count = int(filtered_df["usn"].nunique())
        if subject and not matched_subject:
            return _empty_response(
                f"Subject '{subject}' was not found in the selected dataset.",
                intent="COUNT_STUDENTS_WITH_GRADE",
                meta={"query_type": "aggregation", "grade": grade_value, "subject": subject, "count": 0, "confidence": 1.0},
            )
        subject_text = f" in {matched_subject}" if matched_subject else ""
        return {
            "intent": "COUNT_STUDENTS_WITH_GRADE",
            "answer": f"{count} students got grade {grade_value}{subject_text}.",
            "students": [],
            "meta": {
                "query_type": "aggregation",
                "grade": grade_value,
                "subject": matched_subject or subject or "",
                "count": count,
                "confidence": 1.0,
            },
            "suggestions": [],
        }

    if "subject has the best performance" in normalized or "best performance overall" in normalized:
        if results_df.empty:
            return _empty_response("No subject result data is available.", meta={"query_type": "aggregation"})
        averages = results_df.dropna(subset=["gp"]).groupby("subject")["gp"].mean().sort_values(ascending=False)
        if averages.empty:
            return _empty_response("No GP data is available by subject.", meta={"query_type": "aggregation"})
        return {"intent": "GET_BEST_SUBJECT", "answer": f"{averages.index[0]} has the best overall performance with average GP {float(averages.iloc[0]):.2f}.", "students": [], "meta": {"query_type": "aggregation", "subject": str(averages.index[0]), "average_gp": float(averages.iloc[0]), "confidence": 1.0}, "suggestions": []}

    if "outliers" in normalized or "very high or very low performers" in normalized:
        if not students:
            return _empty_response("No students found.", meta={"query_type": "filter"})
        sgpas = [float(student.sgpa) for student in students]
        avg = sum(sgpas) / len(sgpas)
        matched = [student for student in students if float(student.sgpa) >= 9.0 or float(student.sgpa) <= 4.0]
        return _student_response("GET_OUTLIERS", f"Found {len(matched)} high/low performer outliers using SGPA >= 9.0 or <= 4.0. Class average SGPA is {avg:.2f}.", matched, meta={"query_type": "filter", "confidence": 1.0})

    if "compare performance between subjects" in normalized:
        if results_df.empty:
            return _empty_response("No subject result data is available.", meta={"query_type": "aggregation"})
        averages = results_df.dropna(subset=["gp"]).groupby("subject")["gp"].mean().sort_values(ascending=False)
        if len(averages) < 2:
            return _empty_response("At least two subjects are needed for comparison.", meta={"query_type": "aggregation"})
        return {"intent": "COMPARE_SUBJECT_PERFORMANCE", "answer": f"Best subject: {averages.index[0]} average GP {float(averages.iloc[0]):.2f}. Weakest subject: {averages.index[-1]} average GP {float(averages.iloc[-1]):.2f}.", "students": [], "meta": {"query_type": "aggregation", "best_subject": str(averages.index[0]), "weakest_subject": str(averages.index[-1]), "confidence": 1.0}, "suggestions": []}

    if "summary report" in normalized or "summary of the semester" in normalized:
        summary = _deterministic_contextual_answer("class summary", _build_query_context(db, "class summary", retrieval_mode="sql"))
        if summary:
            return {"intent": "GET_SEMESTER_SUMMARY", "answer": summary["answer"], "students": [], "meta": {"query_type": "contextual", "confidence": summary.get("confidence", 1.0)}, "suggestions": []}

    if normalized in {"student list", "show all students in the dataset", "show all students", "list all students"}:
        return _student_response("GET_ALL_STUDENTS", f"Showing all {len(students)} students in the dataset.", students, meta={"query_type": "filter", "count": len(students), "confidence": 1.0})

    if "list all student usns" in normalized or normalized == "all student usns":
        usns = [student.usn for student in students]
        return {"intent": "GET_ALL_STUDENT_USNS", "answer": "Student USNs: " + ", ".join(usns) + ".", "students": [{"usn": student.usn, "name": student.name, "sgpa": float(student.sgpa)} for student in students], "meta": {"query_type": "lookup", "count": len(usns), "confidence": 1.0}, "suggestions": []}

    if ("how many students" in normalized or "total students" in normalized) and "failed" not in normalized and not grade_value:
        return _execute_aggregation(db, "GET_TOTAL_STUDENTS", {}, 1.0)

    if grade_value and ("records" in normalized or "students" in normalized or "got grade" in normalized or "grade" in normalized):
        subject = _extract_subject_phrase_intel(query) or _extract_subject_phrase(query)
        matched_subject = _match_subject_from_results(results_df, subject)
        filtered_df = results_df[results_df["grade"] == grade_value] if not results_df.empty else results_df

        if matched_subject:
            filtered_df = filtered_df[filtered_df["subject"] == matched_subject]

        matched_usns = list(dict.fromkeys(filtered_df["usn"].astype(str).tolist())) if not filtered_df.empty else []
        student_map = {student.usn: student for student in students}
        matched_students = [student_map[usn] for usn in matched_usns if usn in student_map]

        if not matched_students:
            if subject and not matched_subject:
                return _empty_response(
                    f"Subject '{subject}' was not found in the selected dataset.",
                    intent="GET_STUDENTS_WITH_GRADE",
                    meta={"query_type": "filter", "grade": grade_value, "subject": subject},
                )
            subject_text = f" in {matched_subject}" if matched_subject else ""
            return _empty_response(
                f"No students were found with grade {grade_value}{subject_text}.",
                intent="GET_STUDENTS_WITH_GRADE",
                meta={"query_type": "filter", "grade": grade_value, "subject": matched_subject or subject or ""},
            )

        subject_text = f" in {matched_subject}" if matched_subject else ""
        record_count = int(len(filtered_df)) if not filtered_df.empty else 0
        answer = f"Found {len(matched_students)} students with grade {grade_value}{subject_text}."
        if "records" in normalized:
            answer = f"Found {record_count} subject record(s) with grade {grade_value}{subject_text} across {len(matched_students)} student(s)."
        return {
            "intent": "GET_STUDENTS_WITH_GRADE",
            "answer": answer,
            "students": [_student_summary_dict(student) for student in matched_students],
            "meta": {
                "query_type": "filter",
                "grade": grade_value,
                "subject": matched_subject or subject or "",
                "record_count": record_count,
                "confidence": 1.0,
            },
            "suggestions": [],
        }

    # Handle "students with F in a subject" (generic - no specific subject)
    if ("failed in any subject" in normalized or "at least one f grade" in normalized or "students who failed" in normalized or "find students with at least one f" in normalized or "students with f in a subject" in normalized or "students with f in" in normalized):
        return _execute_filter(db, "GET_FAILED", {}, confidence=1.0)

    if "all passing grades" in normalized:
        return _execute_filter(db, "GET_ALL_PASSING", {}, confidence=1.0)

    if "how many students failed" in normalized or "count how many students failed" in normalized or "count failed students" in normalized:
        return _execute_aggregation(db, "GET_FAILED_COUNT", {}, 1.0)

    if "gp 0" in normalized or "gp 0" in query.lower() or "gp = 0" in query.lower():
        return _execute_filter(db, "GET_GP_ZERO_ANY", {}, confidence=1.0)

    if "average grade point" in normalized or normalized == "average gp per class":
        return _execute_aggregation(db, "GET_AVERAGE_GP", {}, 1.0)

    if "all subjects" in normalized or "subjects available" in normalized:
        if "grade" not in normalized:
            return _execute_aggregation(db, "GET_ALL_SUBJECTS", {}, 1.0)

    if "percentage" in normalized and "passed" in normalized:
        return _execute_aggregation(db, "GET_PASS_PERCENTAGE", {}, 1.0)

    if "percentage" in normalized and "failed" in normalized:
        total = len(students)
        failed = sum(1 for student in students if any(_result_grade(result).upper() == "F" for result in _student_results(student)))
        pct = (failed / total * 100) if total else 0.0
        return {"intent": "GET_FAIL_PERCENTAGE", "answer": f"Fail percentage: {pct:.1f}% ({failed}/{total} students have at least one F grade).", "students": [], "meta": {"query_type": "aggregation", "fail_percentage": round(pct, 2), "failed_count": failed, "total_count": total, "confidence": 1.0}, "suggestions": []}

    gp_values = [float(_result_gp(result)) for student in students for result in _student_results(student) if _result_gp(result) is not None]
    if "highest gp" in normalized:
        highest = max(gp_values) if gp_values else 0.0
        return {"intent": "GET_HIGHEST_GP", "answer": f"The highest GP achieved is {highest:.2f}.", "students": [], "meta": {"query_type": "aggregation", "highest_gp": highest, "confidence": 1.0}, "suggestions": []}
    if "lowest gp" in normalized:
        lowest = min(gp_values) if gp_values else 0.0
        return {"intent": "GET_LOWEST_GP", "answer": f"The lowest GP achieved is {lowest:.2f}.", "students": [], "meta": {"query_type": "aggregation", "lowest_gp": lowest, "confidence": 1.0}, "suggestions": []}

    if "distribution of grades" in normalized or "grade distribution" in normalized:
        distribution = compute_grade_distribution(students)
        answer = "Grade distribution: " + ", ".join(f"{grade}: {count}" for grade, count in sorted(distribution.items())) + "."
        return {"intent": "GET_GRADE_DISTRIBUTION", "answer": answer, "students": [], "meta": {"query_type": "aggregation", "grade_distribution": distribution, "confidence": 1.0}, "suggestions": []}

    if "median sgpa" in normalized or "median of the class" in normalized or "class median" in normalized:
        sgpas = [float(student.sgpa) for student in students if student.sgpa is not None]
        median_sgpa = statistics.median(sgpas) if sgpas else 0.0
        return {"intent": "GET_MEDIAN_SGPA", "answer": f"Median SGPA is {median_sgpa:.2f}.", "students": [], "meta": {"query_type": "aggregation", "median_sgpa": round(float(median_sgpa), 2), "confidence": 1.0}, "suggestions": []}

    if "most difficult" in normalized or "hardest" in normalized or "difficult subjects" in normalized or "most challenging" in normalized:
        subject_rows = sorted(
            _subject_statistics(students),
            key=lambda item: (float(item.get("fail_rate", 0.0)), -float(item.get("average_gp", 0.0))),
            reverse=True,
        )
        preview = subject_rows[:3]
        if preview:
            answer = "The most difficult subjects are: " + "; ".join(f"{item['subject']} (fail rate {float(item.get('fail_rate', 0.0)) * 100:.1f}%, avg GP {float(item.get('average_gp', 0.0)):.2f})" for item in preview) + "."
            return {"intent": "GET_HARDEST_SUBJECTS", "answer": answer, "students": [], "meta": {"query_type": "aggregation", "subjects": preview, "confidence": 1.0}, "suggestions": []}

    if "easiest" in normalized or "easiest subject" in normalized or "best subject" in normalized or "strongest subject" in normalized:
        subject_rows = sorted(_subject_statistics(students), key=lambda item: (float(item.get("fail_rate", 0.0)), -float(item.get("average_gp", 0.0))))
        preview = subject_rows[:3]
        if preview:
            answer = "The easiest subjects are: " + "; ".join(f"{item['subject']} (fail rate {float(item.get('fail_rate', 0.0)) * 100:.1f}%, avg GP {float(item.get('average_gp', 0.0)):.2f})" for item in preview) + "."
            return {"intent": "GET_EASIEST_SUBJECTS", "answer": answer, "students": [], "meta": {"query_type": "aggregation", "subjects": preview, "confidence": 1.0}, "suggestions": []}

    if "highest average gp" in normalized or "top average gp" in normalized or "best average gp" in normalized:
        subject_rows = sorted(_subject_statistics(students), key=lambda item: float(item.get("average_gp", 0.0)), reverse=True)
        if subject_rows:
            best = subject_rows[0]
            return {"intent": "GET_BEST_SUBJECT_BY_GP", "answer": f"{best['subject']} has the highest average GP at {float(best.get('average_gp', 0.0)):.2f}.", "students": [], "meta": {"query_type": "aggregation", "subject": str(best.get('subject')), "average_gp": float(best.get('average_gp', 0.0)), "confidence": 1.0}, "suggestions": []}

    if "lowest average gp" in normalized or "worst average gp" in normalized:
        subject_rows = sorted(_subject_statistics(students), key=lambda item: float(item.get("average_gp", 0.0)))
        if subject_rows:
            worst = subject_rows[0]
            return {"intent": "GET_WORST_SUBJECT_BY_GP", "answer": f"{worst['subject']} has the lowest average GP at {float(worst.get('average_gp', 0.0)):.2f}.", "students": [], "meta": {"query_type": "aggregation", "subject": str(worst.get('subject')), "average_gp": float(worst.get('average_gp', 0.0)), "confidence": 1.0}, "suggestions": []}

    if "weak students" in normalized or "need counseling" in normalized or "needing counseling" in normalized or "at risk" in normalized:
        weak_students = [student for student in students if float(student.sgpa or 0.0) <= 5.0 or any(_result_grade(result).upper() == "F" for result in _student_results(student))]
        if weak_students:
            return _student_response(
                "GET_WEAK_STUDENTS",
                f"Found {len(weak_students)} students who may need counseling support based on SGPA <= 5.0 or at least one F grade.",
                weak_students,
                meta={"query_type": "filter", "confidence": 1.0},
            )

    if "average gp per subject" in normalized:
        if results_df.empty:
            return _empty_response("No subject result data is available.", meta={"query_type": "aggregation"})
        rows = results_df.dropna(subset=["gp"]).groupby("subject")["gp"].mean().sort_values(ascending=False)
        return {"intent": "GET_AVERAGE_GP_PER_SUBJECT", "answer": "Average GP per subject: " + "; ".join(f"{subject}: {value:.2f}" for subject, value in rows.items()) + ".", "students": [], "meta": {"query_type": "aggregation", "subjects": rows.round(2).to_dict(), "confidence": 1.0}, "suggestions": []}

    if "subject has the highest failures" in normalized:
        if results_df.empty:
            return _empty_response("No subject result data is available.", meta={"query_type": "aggregation"})
        failures = results_df[results_df["grade"] == "F"].groupby("subject").size().sort_values(ascending=False)
        if failures.empty:
            return {"intent": "GET_SUBJECT_HIGHEST_FAILURES", "answer": "No subject has failures in the current dataset.", "students": [], "meta": {"query_type": "aggregation", "confidence": 1.0}, "suggestions": []}
        return {"intent": "GET_SUBJECT_HIGHEST_FAILURES", "answer": f"{failures.index[0]} has the highest failures with {int(failures.iloc[0])} F grade record(s).", "students": [], "meta": {"query_type": "aggregation", "subject": str(failures.index[0]), "failures": int(failures.iloc[0]), "confidence": 1.0}, "suggestions": []}

    if "subject has the best performance" in normalized or "best performance overall" in normalized:
        if results_df.empty:
            return _empty_response("No subject result data is available.", meta={"query_type": "aggregation"})
        averages = results_df.dropna(subset=["gp"]).groupby("subject")["gp"].mean().sort_values(ascending=False)
        if averages.empty:
            return _empty_response("No GP data is available by subject.", meta={"query_type": "aggregation"})
        return {"intent": "GET_BEST_SUBJECT", "answer": f"{averages.index[0]} has the best overall performance with average GP {float(averages.iloc[0]):.2f}.", "students": [], "meta": {"query_type": "aggregation", "subject": str(averages.index[0]), "average_gp": float(averages.iloc[0]), "confidence": 1.0}, "suggestions": []}

    if any(marker in normalized for marker in ["total grade points", "sorted by grade points", "top 5 students based on gp", "lowest scoring student", "above average gp", "need improvement", "scholarships", "categories top average poor", "consistent high grades", "rank students"]):
        ranked = sorted(students, key=lambda student: (_total_gp(student), float(student.sgpa)), reverse=True)
        average_total_gp = sum(_total_gp(student) for student in students) / len(students) if students else 0.0
        if "total grade points" in normalized:
            preview = ranked[:20]
            answer = "Total GP by student: " + "; ".join(f"{student.name} ({student.usn}): {_total_gp(student):.2f}" for student in preview) + "."
            return _student_response("GET_TOTAL_GP_BY_STUDENT", answer, preview, meta={"query_type": "aggregation", "confidence": 1.0})
        if "sorted by grade points" in normalized:
            return _student_response("GET_STUDENTS_SORTED_BY_GP", f"Showing students sorted by total GP descending. Class average total GP is {average_total_gp:.2f}.", ranked, meta={"query_type": "aggregation", "confidence": 1.0})
        if "top 5 students based on gp" in normalized or "scholarships" in normalized:
            top = ranked[:5]
            intent = "GET_SCHOLARSHIP_RECOMMENDATIONS" if "scholarships" in normalized else "GET_TOP_5_BY_GP"
            return _student_response(intent, "Top 5 students by total GP: " + "; ".join(f"{student.name} ({student.usn}) total GP {_total_gp(student):.2f}" for student in top) + ".", top, meta={"query_type": "aggregation", "confidence": 1.0})
        if "lowest scoring student" in normalized:
            lowest = list(reversed(ranked))[:1]
            return _student_response("GET_LOWEST_SCORING_STUDENT", f"The lowest scoring student by total GP is {lowest[0].name} ({lowest[0].usn}) with total GP {_total_gp(lowest[0]):.2f}." if lowest else "No students found.", lowest, meta={"query_type": "aggregation", "confidence": 1.0})
        if "above average gp" in normalized:
            matched = [student for student in students if _total_gp(student) > average_total_gp]
            return _student_response("GET_ABOVE_AVERAGE_GP_STUDENTS", f"Found {len(matched)} students above the class average total GP of {average_total_gp:.2f}.", matched, meta={"query_type": "filter", "average_total_gp": average_total_gp, "confidence": 1.0})
        if "need improvement" in normalized:
            threshold_match = re.search(r"(?:less than|below|<)\s*([0-9]+(?:\.[0-9]+)?)", query.lower())
            threshold = float(threshold_match.group(1)) if threshold_match else 5.0
            matched = [student for student in students if _average_gp_for_student(student) < threshold]
            return _student_response("GET_IMPROVEMENT_STUDENTS", f"Found {len(matched)} students with average GP below {threshold:.2f}.", matched, meta={"query_type": "filter", "threshold": threshold, "confidence": 1.0})
        if "categories top average poor" in normalized:
            top_count = average_count = poor_count = 0
            for student in students:
                avg = _average_gp_for_student(student)
                if avg >= 8:
                    top_count += 1
                elif avg >= 5:
                    average_count += 1
                else:
                    poor_count += 1
            return {"intent": "GROUP_STUDENTS_BY_PERFORMANCE", "answer": f"Performance categories: Top {top_count}, Average {average_count}, Poor {poor_count}.", "students": [], "meta": {"query_type": "aggregation", "top": top_count, "average": average_count, "poor": poor_count, "confidence": 1.0}, "suggestions": []}
        if "consistent high grades" in normalized:
            high_grades = {"O", "A+", "A"}
            matched = [student for student in students if _student_results(student) and all(_result_grade(result).upper() in high_grades for result in _student_results(student))]
            return _student_response("GET_CONSISTENT_HIGH_GRADES", f"Found {len(matched)} students with consistent O/A+/A grades across subjects.", matched, meta={"query_type": "filter", "confidence": 1.0})
        if "rank students" in normalized:
            dense_rank = 0
            previous_score = None
            lines = []
            for index, student in enumerate(ranked[:20], 1):
                score = round(_total_gp(student), 2)
                if score != previous_score:
                    dense_rank += 1
                    previous_score = score
                lines.append(f"Rank {dense_rank}: {student.name} ({student.usn}) total GP {score:.2f}")
            return _student_response("RANK_STUDENTS_WITH_TIES", "; ".join(lines) + ".", ranked[:20], meta={"query_type": "aggregation", "confidence": 1.0})

    if "outliers" in normalized or "very high or very low performers" in normalized:
        if not students:
            return _empty_response("No students found.", meta={"query_type": "filter"})
        sgpas = [float(student.sgpa) for student in students]
        avg = sum(sgpas) / len(sgpas)
        matched = [student for student in students if float(student.sgpa) >= 9.0 or float(student.sgpa) <= 4.0]
        return _student_response("GET_OUTLIERS", f"Found {len(matched)} high/low performer outliers using SGPA >= 9.0 or <= 4.0. Class average SGPA is {avg:.2f}.", matched, meta={"query_type": "filter", "confidence": 1.0})

    if "students who improved" in normalized or "student who improved" in normalized:
        improved = []
        for student in students:
            semesters = sorted(student.student_semesters, key=lambda item: item.semester or 0)
            if len(semesters) >= 2 and float(semesters[-1].sgpa) > float(semesters[0].sgpa):
                improved.append(student)
        return _student_response("GET_IMPROVED_STUDENTS", f"Found {len(improved)} students whose latest semester SGPA is higher than their earliest loaded semester SGPA.", improved, meta={"query_type": "filter", "confidence": 1.0})

    if "pass fail trend" in normalized or "pass fail trend" in query.lower():
        total = len(students)
        failed = sum(1 for student in students if any(_result_grade(result).upper() == "F" for result in _student_results(student)))
        passed = total - failed
        return {"intent": "GET_PASS_FAIL_TREND", "answer": f"Current trend: {passed}/{total} students pass all subjects and {failed}/{total} have at least one F grade.", "students": [], "meta": {"query_type": "aggregation", "passed": passed, "failed": failed, "confidence": 1.0}, "suggestions": []}

    if "summary report" in normalized or "summary of the semester" in normalized:
        summary = _deterministic_contextual_answer("class summary", _build_query_context(db, "class summary", retrieval_mode="sql"))
        if summary:
            return {"intent": "GET_SEMESTER_SUMMARY", "answer": summary["answer"], "students": [], "meta": {"query_type": "contextual", "confidence": summary.get("confidence", 1.0)}, "suggestions": []}

    if "missing grades" in normalized or "null or empty values" in normalized or "negative or invalid" in normalized or "anomalies in grading" in normalized:
        missing_rows = []
        invalid_gp_rows = []
        for student in students:
            for result in _student_results(student):
                if not str(_result_subject(result)).strip() or not str(_result_grade(result)).strip():
                    missing_rows.append((student, result))
                gp = _result_gp(result)
                if gp is not None and (float(gp) < 0 or float(gp) > 10):
                    invalid_gp_rows.append((student, result))
        answer = f"Data quality check: {len(missing_rows)} records have missing subject/grade values and {len(invalid_gp_rows)} records have invalid GP values outside 0-10."
        return {"intent": "GET_DATA_QUALITY_CHECK", "answer": answer, "students": [], "meta": {"query_type": "filter", "missing_records": len(missing_rows), "invalid_gp_records": len(invalid_gp_rows), "confidence": 1.0}, "suggestions": []}

    if "duplicate student ids" in normalized or "duplicate student" in normalized:
        usn_counts = Counter(student.usn.upper() for student in students)
        duplicates = [usn for usn, count in usn_counts.items() if count > 1]
        return {"intent": "GET_DUPLICATE_STUDENT_IDS", "answer": f"Found {len(duplicates)} duplicate student ID(s)." + (f" Duplicates: {', '.join(duplicates)}." if duplicates else ""), "students": [], "meta": {"query_type": "filter", "duplicates": duplicates, "confidence": 1.0}, "suggestions": []}

    if "all f grades" in normalized:
        matched = [student for student in students if _student_results(student) and all(_result_grade(result).upper() == "F" for result in _student_results(student))]
        return _student_response("GET_ALL_F_STUDENTS", f"Found {len(matched)} students with all F grades.", matched, meta={"query_type": "filter", "confidence": 1.0})

    if "compare performance between subjects" in normalized:
        if results_df.empty:
            return _empty_response("No subject result data is available.", meta={"query_type": "aggregation"})
        averages = results_df.dropna(subset=["gp"]).groupby("subject")["gp"].mean().sort_values(ascending=False)
        if len(averages) < 2:
            return _empty_response("At least two subjects are needed for comparison.", meta={"query_type": "aggregation"})
        return {"intent": "COMPARE_SUBJECT_PERFORMANCE", "answer": f"Best subject: {averages.index[0]} average GP {float(averages.iloc[0]):.2f}. Weakest subject: {averages.index[-1]} average GP {float(averages.iloc[-1]):.2f}.", "students": [], "meta": {"query_type": "aggregation", "best_subject": str(averages.index[0]), "weakest_subject": str(averages.index[-1]), "confidence": 1.0}, "suggestions": []}

    return None


def _execute_generic_grounded_filter_query(db: Session, query: str, owner_user_id: Optional[int] = None) -> Optional[Dict[str, object]]:
    """Handle common filter queries deterministically without relying on intent creation."""
    lowered = query.lower()
    normalized = _normalize_text(query)

    if "students" not in normalized and "student" not in normalized:
        return None

    if ("gp" in normalized or "grade point" in normalized) and re.search(r"\b0(?:\.0+)?\b", normalized):
        return _execute_filter(db, "GET_GP_ZERO_ANY", {}, confidence=0.95, owner_user_id=owner_user_id)

    if "sgpa" in normalized and _is_sgpa_range_query(query):
        range_entities = _extract_sgpa_range(query)
        if range_entities:
            return _execute_filter(db, "GET_SGPA_RANGE", range_entities, confidence=0.95, owner_user_id=owner_user_id)

    grade_value = _extract_grade_value(query)
    grade_query_markers = [
        "students with",
        "students who got",
        "students having",
        "grade",
    ]
    if grade_value and any(marker in lowered for marker in grade_query_markers):
        return _execute_filter(db, "GET_STUDENTS_WITH_GRADE", {"grade": grade_value}, confidence=0.95, owner_user_id=owner_user_id)

    return None


def _execute_lookup(db: Session, query: str, intent: str, entities: Dict[str, object], confidence: float, owner_user_id: Optional[int] = None) -> Dict[str, object]:
    if intent == "GET_RESULT_BY_NAME":
        student_name = str(entities.get("name") or "").strip()
        if not student_name:
            inferred_students = _find_students_from_query_or_history(db, query, owner_user_id=owner_user_id)
            if inferred_students:
                if len(inferred_students) == 1:
                    answer = _answer_for_single_student_query(query, inferred_students[0])
                else:
                    answer = f"Found {len(inferred_students)} student records that match your request."
                return _student_response(
                    intent,
                    answer,
                    inferred_students,
                    meta={"query_type": "lookup", "inferred_from_query": True, "confidence": confidence, "include_details": True},
                )

            return _empty_response(
                "Please include the student name, for example 'result of Abir'.",
                suggestions=["result of Abir", "result of Ananya", "result of Rahul"],
                intent=intent,
                meta={"query_type": "lookup"},
            )
        matched_students = _lookup_students_by_name_local(db, student_name, owner_user_id=owner_user_id)
        hybrid_meta = {"hybrid_scores": {}, "es_candidates": 0, "semantic_candidates": 0}
        if not matched_students:
            hybrid = _hybrid_lookup_by_name(db, query, student_name, owner_user_id=owner_user_id)
            matched_students = hybrid["students"]
            hybrid_meta = hybrid["meta"]
        if not matched_students:
            return _empty_response(f"No students matched the name '{student_name}'.", intent=intent, meta={"query_type": "lookup"})
        answer = f"Found {len(matched_students)} result record(s) matching '{student_name}'."
        if len(matched_students) == 1:
            answer = _answer_for_single_student_query(query, matched_students[0])
        return _student_response(
            intent,
            answer,
            matched_students,
            meta={"query_type": "lookup", "requested_name": student_name, "confidence": confidence, **hybrid_meta, "include_details": len(matched_students) == 1},
        )

    if intent == "GET_RESULT_BY_USN":
        usn = str(entities.get("usn") or "").strip().upper()
        if not usn:
            return _empty_response(
                "Please include a full USN, for example 'show 1MS21CS001'.",
                suggestions=["show 1MS21CS001", "student with USN 1MS21CS010"],
                intent=intent,
                meta={"query_type": "lookup"},
            )
        student = fetch_student_by_usn(db, usn, owner_user_id=owner_user_id)
        if not student:
            return _empty_response(f"No student was found with USN {usn}.", intent=intent, meta={"query_type": "lookup"})
        return _student_response(
            intent,
            _answer_for_single_student_query(query, student),
            [student],
            meta={"query_type": "lookup", "usn": usn, "confidence": confidence, "include_details": True},
        )

    if intent == "GET_USN_PREFIX":
        prefix = str(entities.get("prefix") or "").strip().upper()
        if not prefix:
            return _empty_response(
                "Please include a USN prefix, for example 'USN prefix 1MS22'.",
                suggestions=["USN prefix 1MS22", "USN prefix 4AL", "USN starts with 1RV"],
                intent=intent,
                meta={"query_type": "lookup"},
            )
        students = fetch_students(db, owner_user_id=owner_user_id)
        matched_students = [student for student in students if student.usn.upper().startswith(prefix)]
        if not matched_students:
            return _empty_response(f"No students were found with USN prefix {prefix}.", intent=intent, meta={"query_type": "lookup"})
        return _student_response(
            intent,
            f"Found {len(matched_students)} students with USN prefix {prefix}.",
            matched_students,
            meta={"query_type": "lookup", "prefix": prefix, "confidence": confidence},
        )

    if intent == "GET_NAME_PREFIX":
        prefix = str(entities.get("prefix") or "").strip()
        if not prefix:
            return _empty_response(
                "Please include a name prefix, for example 'name prefix An'.",
                suggestions=["name prefix An", "name prefix Ra", "name starts with Pri"],
                intent=intent,
                meta={"query_type": "lookup"},
            )
        students = fetch_students(db, owner_user_id=owner_user_id)
        matched_students = [student for student in students if student.name.lower().startswith(prefix.lower())]
        if not matched_students:
            return _empty_response(f"No students were found with name prefix '{prefix}'.", intent=intent, meta={"query_type": "lookup"})
        return _student_response(
            intent,
            f"Found {len(matched_students)} students with names starting with '{prefix}'.",
            matched_students,
            meta={"query_type": "lookup", "prefix": prefix, "confidence": confidence},
        )

    return _empty_response("That lookup query is not implemented yet.", intent=intent, meta={"query_type": "lookup"})


def _execute_filter(db: Session, intent: str, entities: Dict[str, object], confidence: float, owner_user_id: Optional[int] = None) -> Dict[str, object]:
    students = fetch_students(db, owner_user_id=owner_user_id)
    students_df = build_students_dataframe(students)
    results_df = build_results_dataframe(students)

    if intent == "GET_FAILED":
        filtered_df = students_df[students_df["has_fail"]].sort_values(["sgpa", "name"], ascending=[True, True])
        matched_students = _students_from_dataframe(db, filtered_df)
        if not matched_students:
            return _empty_response("No failed students were found in the current dataset.", intent=intent, meta={"query_type": "filter"})
        return _student_response(
            intent,
            f"Found {len(matched_students)} students with at least one failing grade.",
            matched_students,
            meta={"query_type": "filter", "count": len(matched_students), "confidence": confidence},
        )

    if intent == "GET_FAILED_IN_SUBJECT":
        subject = str(entities.get("subject") or "").strip()
        if not subject:
            return _empty_response(
                "Please specify a subject, for example 'students who failed in <subject name>'.",
                suggestions=["students who failed in <subject name>", "students with F in <subject name>"],
                intent=intent,
                meta={"query_type": "filter"},
            )
        
        # Try to find matching subjects in the results
        available_subjects = sorted({str(s).strip() for s in results_df["subject"].dropna() if str(s).strip()})
        
        # Normalize subject search
        normalized_subject_query = _normalize_text(subject)
        best_match = None
        best_match_score = 0
        
        for available_subject in available_subjects:
            normalized_available = _normalize_text(available_subject)
            # Check for exact match or substring match
            if normalized_available == normalized_subject_query:
                best_match = available_subject
                best_match_score = 100
                break
            # Check if query is contained in subject
            if normalized_subject_query in normalized_available:
                match_score = len(normalized_subject_query) * 10 / len(normalized_available)
                if match_score > best_match_score:
                    best_match = available_subject
                    best_match_score = match_score
            # Check if subject is contained in query
            elif normalized_available in normalized_subject_query:
                match_score = len(normalized_available) * 5
                if match_score > best_match_score:
                    best_match = available_subject
                    best_match_score = match_score
        
        if not best_match:
            suggestion_subjects = available_subjects[:5] if available_subjects else []
            suggestions = [f"students who failed in {subj}" for subj in suggestion_subjects]
            return _empty_response(
                f"Subject '{subject}' not found in the dataset.",
                suggestions=suggestions if suggestions else ["students who failed"],
                intent=intent,
                meta={"query_type": "filter", "available_subjects": available_subjects},
            )
        
        # Filter by subject and grade F
        filtered_df = results_df[
            (results_df["subject"] == best_match) &
            (results_df["grade"] == "F")
        ]
        matched_students = _students_from_dataframe(db, filtered_df.drop_duplicates("usn"))
        
        if not matched_students:
            return _empty_response(
                f"No students failed in '{best_match}'.",
                intent=intent,
                meta={"query_type": "filter", "subject": best_match, "confidence": confidence},
            )
        
        # Sort by SGPA to show worst performers first
        matched_students.sort(key=lambda s: float(s.sgpa), reverse=False)
        
        return _student_response(
            intent,
            f"Found {len(matched_students)} students who failed in {best_match}:",
            matched_students,
            meta={"query_type": "filter", "subject": best_match, "count": len(matched_students), "confidence": confidence},
        )

    if intent == "GET_PASSED_IN_SUBJECT":
        subject = str(entities.get("subject") or "").strip()
        if not subject:
            return _empty_response(
                "Please specify a subject, for example 'students who passed in <subject name>'.",
                suggestions=["students who passed in <subject name>", "students without F in <subject name>"],
                intent=intent,
                meta={"query_type": "filter"},
            )
        
        # Try to find matching subjects in the results
        available_subjects = sorted({str(s).strip() for s in results_df["subject"].dropna() if str(s).strip()})
        
        # Normalize subject search
        normalized_subject_query = _normalize_text(subject)
        best_match = None
        best_match_score = 0
        
        for available_subject in available_subjects:
            normalized_available = _normalize_text(available_subject)
            if normalized_available == normalized_subject_query:
                best_match = available_subject
                best_match_score = 100
                break
            if normalized_subject_query in normalized_available:
                match_score = len(normalized_subject_query) * 10 / len(normalized_available)
                if match_score > best_match_score:
                    best_match = available_subject
                    best_match_score = match_score
            elif normalized_available in normalized_subject_query:
                match_score = len(normalized_available) * 5
                if match_score > best_match_score:
                    best_match = available_subject
                    best_match_score = match_score
        
        if not best_match:
            suggestion_subjects = available_subjects[:5] if available_subjects else []
            suggestions = [f"students who passed in {subj}" for subj in suggestion_subjects]
            return _empty_response(
                f"Subject '{subject}' not found in the dataset.",
                suggestions=suggestions if suggestions else ["students who passed"],
                intent=intent,
                meta={"query_type": "filter", "available_subjects": available_subjects},
            )
        
        # Filter by subject and NOT grade F
        filtered_df = results_df[
            (results_df["subject"] == best_match) &
            (results_df["grade"] != "F")
        ]
        matched_students = _students_from_dataframe(db, filtered_df.drop_duplicates("usn"))
        
        if not matched_students:
            return _empty_response(
                f"No students passed in '{best_match}'.",
                intent=intent,
                meta={"query_type": "filter", "subject": best_match, "confidence": confidence},
            )
        
        # Sort by SGPA to show top performers first
        matched_students.sort(key=lambda s: float(s.sgpa), reverse=True)
        
        return _student_response(
            intent,
            f"Found {len(matched_students)} students who passed in {best_match}:",
            matched_students,
            meta={"query_type": "filter", "subject": best_match, "count": len(matched_students), "confidence": confidence},
        )

    if intent == "GET_SGPA_RANGE":
        min_sgpa = float(entities.get("min_sgpa") or 0.0)
        max_sgpa = float(entities.get("max_sgpa") or 10.0)
        
        if min_sgpa < 0 or max_sgpa > 10 or min_sgpa > max_sgpa:
            return _empty_response(
                f"Invalid SGPA range: {min_sgpa}-{max_sgpa}. Valid range is 0-10.",
                intent=intent,
                meta={"query_type": "filter"},
            )
        
        # Filter by SGPA range
        filtered_students = [
            student for student in students
            if min_sgpa <= float(student.sgpa or 0.0) <= max_sgpa
        ]
        
        if not filtered_students:
            return _empty_response(
                f"No students found with SGPA between {min_sgpa} and {max_sgpa}.",
                intent=intent,
                meta={"query_type": "filter", "min_sgpa": min_sgpa, "max_sgpa": max_sgpa},
            )
        
        # Sort by SGPA (highest first)
        filtered_students.sort(key=lambda s: float(s.sgpa or 0.0), reverse=True)
        
        return _student_response(
            intent,
            f"Found {len(filtered_students)} students with SGPA between {min_sgpa} and {max_sgpa}:",
            filtered_students,
            meta={
                "query_type": "filter",
                "min_sgpa": min_sgpa,
                "max_sgpa": max_sgpa,
                "count": len(filtered_students),
                "confidence": confidence,
            },
        )

    if intent == "GET_STUDENTS_WITH_GRADE":
        grade = str(entities.get("grade") or "").strip().upper()
        # Subject is extracted by intelligence module and passed in entities
        subject = str(entities.get("subject") or "").strip()
        
        if not grade:
            return _empty_response(
                "Please specify a grade, for example 'students with A+'.",
                suggestions=["students with A+", "students with O", "students with F"],
                intent=intent,
                meta={"query_type": "filter"},
            )
        filtered_df = results_df[results_df["grade"] == grade]
        
        # If subject is specified, apply subject filtering
        best_match = None
        if subject:
            available_subjects = sorted({str(s).strip() for s in results_df["subject"].dropna() if str(s).strip()})
            normalized_subject_query = _normalize_text(subject)
            best_match_score = 0
            
            for available_subject in available_subjects:
                normalized_available = _normalize_text(available_subject)
                # Check for exact match
                if normalized_available == normalized_subject_query:
                    best_match = available_subject
                    best_match_score = 100
                    break
                # Check if query is contained in subject
                if normalized_subject_query in normalized_available:
                    match_score = len(normalized_subject_query) * 10 / len(normalized_available)
                    if match_score > best_match_score:
                        best_match = available_subject
                        best_match_score = match_score
                # Check if subject is contained in query
                elif normalized_available in normalized_subject_query:
                    match_score = len(normalized_available) * 5
                    if match_score > best_match_score:
                        best_match = available_subject
                        best_match_score = match_score
            
            if best_match:
                filtered_df = filtered_df[filtered_df["subject"] == best_match]
        
        matched_students = _students_from_dataframe(db, filtered_df.drop_duplicates("usn"))
        if not matched_students:
            msg = f"No students were found with grade {grade}"
            if subject:
                msg += f" in {subject}"
            msg += "."
            return _empty_response(msg, intent=intent, meta={"query_type": "filter"})
        
        answer_text = f"Found {len(matched_students)} students with grade {grade}"
        if subject and best_match:
            answer_text += f" in {best_match}"
        answer_text += "."
        
        return _student_response(
            intent,
            answer_text,
            matched_students,
            meta={"query_type": "filter", "grade": grade, "subject": best_match or subject, "confidence": confidence},
        )

    if intent == "GET_GRADE_BUT_FAILED":
        grade = str(entities.get("grade") or "A+").strip().upper()
        filtered_df = students_df[
            (students_df["has_fail"])
            & (students_df["has_a_plus"] if grade == "A+" else students_df["grade_set"].map(lambda grades: grade in grades))
        ]
        matched_students = _students_from_dataframe(db, filtered_df)
        if not matched_students:
            return _empty_response(
                f"No students were found with grade {grade} and a failure in another subject.",
                intent=intent,
                meta={"query_type": "filter"},
            )
        return _student_response(
            intent,
            f"Found {len(matched_students)} students with grade {grade} who also failed another subject.",
            matched_students,
            meta={"query_type": "filter", "grade": grade, "confidence": confidence},
        )

    if intent == "GET_INCONSISTENT_PERFORMERS":
        if results_df.empty:
            return _empty_response("No detailed subject data is available to detect inconsistent performers.", intent=intent, meta={"query_type": "filter"})
        stats_df = (
            results_df.dropna(subset=["gp"])
            .groupby(["usn", "name"], as_index=False)
            .agg(
                max_gp=("gp", "max"),
                min_gp=("gp", "min"),
                gp_std=("gp", "std"),
                fail_count=("grade", lambda values: int((values == "F").sum())),
                subject_count=("subject", "count"),
            )
        )
        stats_df["gp_spread"] = stats_df["max_gp"] - stats_df["min_gp"]
        filtered_df = stats_df[
            (stats_df["subject_count"] >= 2)
            & (
                (stats_df["gp_spread"] >= 6)
                | ((stats_df["gp_std"].fillna(0.0) >= 3.0) & (stats_df["fail_count"] >= 1))
            )
        ].sort_values(["gp_spread", "gp_std"], ascending=[False, False])
        matched_students = _students_from_dataframe(db, filtered_df)
        if not matched_students:
            return _empty_response("No inconsistent performers were detected in the current dataset.", intent=intent, meta={"query_type": "filter"})
        return _student_response(
            intent,
            f"Found {len(matched_students)} students with highly uneven subject performance.",
            matched_students,
            meta={"query_type": "filter", "confidence": confidence},
        )

    if intent == "GET_GP_ZERO_WITH_A":
        filtered_df = students_df[
            (students_df["has_gp_zero"])
            & (students_df["has_a_plus"] | students_df["has_a_grade"])
        ]
        matched_students = _students_from_dataframe(db, filtered_df)
        if not matched_students:
            return _empty_response("No students were found with GP = 0 and A-range grades together.", intent=intent, meta={"query_type": "filter"})
        return _student_response(
            intent,
            f"Found {len(matched_students)} students with GP = 0 in one subject and A-range grades elsewhere.",
            matched_students,
            meta={"query_type": "filter", "confidence": confidence},
        )

    if intent == "GET_GP_ZERO_ANY":
        filtered_df = students_df[students_df["has_gp_zero"]]
        matched_students = _students_from_dataframe(db, filtered_df)
        if not matched_students:
            return _empty_response("No students were found with GP = 0 in any subject.", intent=intent, meta={"query_type": "filter"})
        return _student_response(
            intent,
            f"Found {len(matched_students)} students with GP = 0 in at least one subject.",
            matched_students,
            meta={"query_type": "filter", "confidence": confidence},
        )

    if intent == "GET_ALL_STUDENTS":
        return _student_response(
            intent,
            f"Showing all {len(students)} students in the current dataset.",
            students,
            meta={"query_type": "filter", "count": len(students), "confidence": confidence},
        )

    if intent == "GET_ALL_PASSING":
        filtered_df = students_df[~students_df["has_fail"]].sort_values(["sgpa", "name"], ascending=[False, True])
        matched_students = _students_from_dataframe(db, filtered_df)
        if not matched_students:
            return _empty_response("No students with all passing grades were found.", intent=intent, meta={"query_type": "filter"})
        return _student_response(
            intent,
            f"Found {len(matched_students)} students with all passing grades and no F subjects.",
            matched_students,
            meta={"query_type": "filter", "confidence": confidence},
        )

    return _empty_response("That filter query is not implemented yet.", intent=intent, meta={"query_type": "filter"})


def _execute_aggregation(db: Session, intent: str, entities: Dict[str, object], confidence: float, owner_user_id: Optional[int] = None) -> Dict[str, object]:
    students = fetch_students(db, owner_user_id=owner_user_id)

    if intent == "GET_TOPPER":
        topper = fetch_topper(db, owner_user_id=owner_user_id)
        if not topper:
            return _empty_response("No topper could be computed from the current dataset.", intent=intent, meta={"query_type": "aggregation"})
        return _student_response(
            intent,
            f"{topper.name} is the topper with SGPA {float(topper.sgpa):.2f}.",
            [topper],
            meta={"query_type": "aggregation", "confidence": confidence},
        )

    if intent == "GET_AVERAGE_SGPA":
        average = compute_average_sgpa(db)
        return {
            "intent": intent,
            "answer": f"The average SGPA is {average:.2f}.",
            "students": [],
            "meta": {"query_type": "aggregation", "average_sgpa": average, "confidence": confidence},
            "suggestions": [],
        }

    if intent == "GET_AVERAGE_GP":
        average_gp = compute_average_gp(students)
        return {
            "intent": intent,
            "answer": f"The average grade point (GP) is {average_gp:.2f}.",
            "students": [],
            "meta": {"query_type": "aggregation", "average_gp": average_gp, "confidence": confidence},
            "suggestions": [],
        }

    if intent == "GET_TOP_N":
        limit = int(entities.get("limit") or 5)
        top_students = fetch_top_students(db, limit, owner_user_id=owner_user_id)
        return _student_response(
            intent,
            f"Showing the top {len(top_students)} students by SGPA.",
            top_students,
            meta={"query_type": "aggregation", "limit": limit, "confidence": confidence},
        )

    if intent == "GET_FAILED_COUNT":
        failed_count = sum(1 for student in students if any((result.grade or "").upper() == "F" for result in student.results))
        return {
            "intent": intent,
            "answer": f"{failed_count} students failed in at least one subject.",
            "students": [],
            "meta": {"query_type": "aggregation", "failed_count": failed_count, "confidence": confidence},
            "suggestions": [],
        }

    if intent == "GET_TOTAL_STUDENTS":
        total_students = len(students)
        return {
            "intent": intent,
            "answer": f"There are {total_students} students in the current dataset.",
            "students": [],
            "meta": {"query_type": "aggregation", "total_students": total_students, "confidence": confidence},
            "suggestions": [],
        }

    if intent == "GET_MOST_FREQUENT_GRADE":
        distribution = compute_grade_distribution(students)
        if not distribution:
            return _empty_response("No grade data is available to compute the most frequent grade.", intent=intent, meta={"query_type": "aggregation"})
        grade, count = max(distribution.items(), key=lambda item: item[1])
        return {
            "intent": intent,
            "answer": f"The most frequent grade is {grade}, appearing {count} times.",
            "students": [],
            "meta": {"query_type": "aggregation", "grade": grade, "count": count, "confidence": confidence},
            "suggestions": [],
        }

    if intent == "GET_ALL_SUBJECTS":
        results_df = build_results_dataframe(students)
        if results_df.empty or "subject" not in results_df.columns:
            return _empty_response("No subject data is available to list.", intent=intent, meta={"query_type": "aggregation"})
        subjects = sorted({str(subject).strip() for subject in results_df["subject"].dropna() if str(subject).strip()})
        if not subjects:
            return _empty_response("No subject data is available to list.", intent=intent, meta={"query_type": "aggregation"})
        return {
            "intent": intent,
            "answer": "Subjects in the dataset: " + ", ".join(subjects) + ".",
            "students": [],
            "meta": {
                "query_type": "aggregation",
                "subject_count": len(subjects),
                "subjects": subjects,
                "confidence": confidence,
            },
            "suggestions": [],
        }

    if intent == "GET_PASS_PERCENTAGE":
        total_students = len(students)
        if total_students == 0:
            return _empty_response("No students in dataset.", intent=intent, meta={"query_type": "aggregation"})
        
        # Count students with no F grades (passed all subjects)
        passed_students = sum(
            1 for student in students
            if not any((result.grade or "").upper() == "F" for result in student.results)
        )
        
        pass_percentage = (passed_students / total_students) * 100 if total_students > 0 else 0.0
        
        return {
            "intent": intent,
            "answer": f"Pass percentage: {pass_percentage:.1f}% ({passed_students}/{total_students} students passed all subjects).",
            "students": [],
            "meta": {
                "query_type": "aggregation",
                "pass_percentage": round(pass_percentage, 2),
                "passed_count": passed_students,
                "total_count": total_students,
                "confidence": confidence,
            },
            "suggestions": [],
        }

    return _empty_response("That aggregation query is not implemented yet.", intent=intent, meta={"query_type": "aggregation"})


def _deterministic_contextual_answer(query: str, context: Dict[str, object]) -> Optional[Dict[str, object]]:
    lowered = query.lower()
    summary = context.get("summary", {})
    students = context.get("students", [])
    subject_statistics = context.get("subject_statistics", [])
    retrieved_chunks = context.get("retrieved_chunks", [])
    if not isinstance(summary, dict) or not isinstance(students, list):
        return None

    if students:
        lead_student = students[0]
        if any(term in lowered for term in ["his grades", "her grades", "their grades", "what about his grades", "what about her grades"]):
            grades = ", ".join(result["grade"] for result in lead_student.get("results", [])) or "no recorded grades"
            return {
                "answer": f"{lead_student['name']} received these grades: {grades}.",
                "student_usns": [str(lead_student["usn"]).upper()],
                "confidence": 0.4,
            }
        if "his sgpa" in lowered or "her sgpa" in lowered:
            return {
                "answer": f"{lead_student['name']} has SGPA {float(lead_student['sgpa']):.2f}.",
                "student_usns": [str(lead_student["usn"]).upper()],
                "confidence": 0.4,
            }

    if any(term in lowered for term in ["overall", "summary", "summarize", "class performance", "class overview", "cohort"]):
        topper = summary.get("topper") or {}
        topper_name = topper.get("name", "N/A")
        topper_sgpa = topper.get("sgpa", "N/A")
        answer = (
            f"The dataset contains {summary.get('total_students', 0)} students. "
            f"The average SGPA is {summary.get('average_sgpa', 0):.2f}, "
            f"the average grade point is {summary.get('average_gp', 0):.2f}, "
            f"the topper is {topper_name} with SGPA {topper_sgpa}, "
            f"and {summary.get('failed_count', 0)} students have at least one failing grade."
        )
        topper_usn = topper.get("usn")
        return {
            "answer": answer,
            "student_usns": [str(topper_usn).upper()] if topper_usn else [],
            "confidence": 0.45,
        }

    if any(term in lowered for term in ["median sgpa", "median of the class", "class median"]):
        sgpas = [float(student.get("sgpa", 0.0) or 0.0) for student in students if isinstance(student, dict)]
        if sgpas:
            return {
                "answer": f"The median SGPA is {statistics.median(sgpas):.2f}.",
                "student_usns": [],
                "confidence": 0.55,
            }

    subject_rows = [row for row in subject_statistics if isinstance(row, dict)]

    if any(term in lowered for term in ["most difficult", "hardest", "difficult subject", "difficult subjects", "most challenging", "low performance subject"]):
        challenging = summary.get("challenging_subjects", [])
        if isinstance(challenging, list) and challenging:
            lines = [
                f"{item['subject']} (fail rate {float(item.get('fail_rate', 0.0)) * 100:.1f}%, avg GP {float(item.get('average_gp', 0.0)):.2f})"
                for item in challenging[:3]
            ]
            return {
                "answer": "The hardest-looking subjects by failures and low performance are: " + "; ".join(lines) + ".",
                "student_usns": [],
                "confidence": 0.5,
            }

    if any(term in lowered for term in ["easiest subject", "easiest subjects", "easiest", "best subject", "strongest subject"]):
        if subject_rows:
            ranked = sorted(subject_rows, key=lambda item: (float(item.get("fail_rate", 0.0)), -float(item.get("average_gp", 0.0))))
            lines = [
                f"{item['subject']} (fail rate {float(item.get('fail_rate', 0.0)) * 100:.1f}%, avg GP {float(item.get('average_gp', 0.0)):.2f})"
                for item in ranked[:3]
            ]
            return {
                "answer": "The easiest subjects by low failures and high GP are: " + "; ".join(lines) + ".",
                "student_usns": [],
                "confidence": 0.55,
            }

    if any(term in lowered for term in ["highest average gp", "top average gp", "best average gp"]):
        if subject_rows:
            best = max(subject_rows, key=lambda item: float(item.get("average_gp", 0.0)))
            return {
                "answer": f"{best['subject']} has the highest average GP at {float(best.get('average_gp', 0.0)):.2f}.",
                "student_usns": [],
                "confidence": 0.6,
            }

    if any(term in lowered for term in ["lowest average gp", "worst average gp"]):
        if subject_rows:
            worst = min(subject_rows, key=lambda item: float(item.get("average_gp", 0.0)))
            return {
                "answer": f"{worst['subject']} has the lowest average GP at {float(worst.get('average_gp', 0.0)):.2f}.",
                "student_usns": [],
                "confidence": 0.6,
            }

    if any(term in lowered for term in ["weak students", "need counseling", "needing counseling", "at risk"]):
        if students:
            weak_students = [
                student for student in students
                if float(student.get("sgpa", 0.0) or 0.0) <= 5.0 or any((result.get("grade") or "").upper() == "F" for result in student.get("results", []))
            ]
            if weak_students:
                preview = weak_students[:10]
                return {
                    "answer": f"Found {len(weak_students)} students who may need counseling support based on SGPA <= 5.0 or at least one F grade.",
                    "student_usns": [str(student.get("usn", "")).upper() for student in preview if student.get("usn")],
                    "confidence": 0.55,
                }

    subject_contrast = _extract_contrast_subject_phrases(query)
    if subject_contrast and students:
        stronger_subject, weaker_subject = subject_contrast
        comparison_rows = []
        for student in students:
            stronger_match = _best_subject_match(student, stronger_subject)
            weaker_match = _best_subject_match(student, weaker_subject)
            if not stronger_match or not weaker_match:
                continue
            if _result_gp(stronger_match) is None or _result_gp(weaker_match) is None:
                continue
            gap = float(_result_gp(stronger_match) or 0.0) - float(_result_gp(weaker_match) or 0.0)
            if gap >= 10:
                comparison_rows.append((gap, student, stronger_match, weaker_match))
        comparison_rows.sort(key=lambda item: item[0], reverse=True)
        if comparison_rows:
            preview = comparison_rows[:3]
            answer = "; ".join(
                f"{_student_name(student)}: {_result_subject(stronger_match)} GP {float(_result_gp(stronger_match) or 0.0):.2f} vs {_result_subject(weaker_match)} GP {float(_result_gp(weaker_match) or 0.0):.2f}"
                for _, student, stronger_match, weaker_match in preview
            )
            return {
                "answer": f"Students who appear stronger in {stronger_subject} but weaker in {weaker_subject} include {answer}.",
                "student_usns": [_student_usn(student) for _, student, _, _ in preview],
                "confidence": 0.5,
            }

    if any(term in lowered for term in ["trend", "trends"]) and isinstance(subject_statistics, list) and subject_statistics:
        challenging = summary.get("challenging_subjects", [])
        trend_bits = []
        if challenging:
            trend_bits.append(
                "the most challenging subjects are "
                + ", ".join(str(item.get("subject", "N/A")) for item in challenging[:3])
            )
        grade_distribution = summary.get("grade_distribution", {})
        if isinstance(grade_distribution, dict) and grade_distribution:
            top_grade, top_count = max(grade_distribution.items(), key=lambda item: item[1])
            trend_bits.append(f"grade {top_grade} appears most often with {top_count} occurrences")
        if trend_bits:
            return {
                "answer": "Performance trends suggest that " + ", and ".join(trend_bits) + ".",
                "student_usns": [],
                "confidence": 0.48,
            }

    if "compare" in lowered and "topper" in lowered and "failed" in lowered:
        topper = summary.get("topper") or {}
        topper_name = topper.get("name", "N/A")
        topper_sgpa = topper.get("sgpa", "N/A")
        answer = (
            f"The topper is {topper_name} with SGPA {topper_sgpa}. "
            f"In contrast, {summary.get('failed_count', 0)} students have at least one F grade. "
            f"The class average SGPA is {summary.get('average_sgpa', 0):.2f}, so the gap between the top performer "
            f"and the struggling group is substantial."
        )
        topper_usn = topper.get("usn")
        return {
            "answer": answer,
            "student_usns": [str(topper_usn).upper()] if topper_usn else [],
            "confidence": 0.5,
        }

    if any(term in lowered for term in ["insight", "insights", "trend", "trends"]):
        distribution = summary.get("grade_distribution", {})
        top_grade = "N/A"
        top_grade_count = 0
        if isinstance(distribution, dict) and distribution:
            top_grade, top_grade_count = max(distribution.items(), key=lambda item: item[1])
        topper = summary.get("topper") or {}
        answer = (
            f"The class average SGPA is {summary.get('average_sgpa', 0):.2f} across {summary.get('total_students', 0)} students. "
            f"The strongest signal is that grade {top_grade} appears most often with {top_grade_count} occurrences, "
            f"while {summary.get('failed_count', 0)} students have at least one failing grade. "
            f"The topper is {topper.get('name', 'N/A')} with SGPA {topper.get('sgpa', 'N/A')}."
        )
        topper_usn = topper.get("usn")
        return {
            "answer": answer,
            "student_usns": [str(topper_usn).upper()] if topper_usn else [],
            "confidence": 0.5,
        }

    if any(term in lowered for term in ["recommend", "recommendation", "scholarship", "scholarships"]) and students:
        ranked = sorted(students, key=lambda item: float(item.get("sgpa", 0.0) or 0.0), reverse=True)[:5]
        if ranked:
            lines = [
                f"{student.get('name', 'N/A')} ({student.get('usn', 'N/A')}) with SGPA {float(student.get('sgpa', 0.0) or 0.0):.2f}"
                for student in ranked
            ]
            return {
                "answer": "Scholarship candidates from the loaded data: " + "; ".join(lines) + ". They are recommended because they have the strongest SGPA among the available student records.",
                "student_usns": [str(student.get("usn", "")).upper() for student in ranked if student.get("usn")],
                "confidence": 0.5,
            }

    return None


def _validate_llm_response_against_context(llm_resp: Dict[str, object], context: Dict[str, object]) -> bool:
    """Validate LLM response against deterministic context values for key numeric claims.

    Returns True if response is consistent or contains dataset-backed citations/usns; False otherwise.
    """
    if not isinstance(llm_resp, dict):
        return False

    citations = llm_resp.get("citations", [])
    has_citations = isinstance(citations, list) and any(str(item).strip() for item in citations)
    usns = llm_resp.get("student_usns", [])
    has_usns = isinstance(usns, list) and any(str(item).strip() for item in usns)

    answer_text = str(llm_resp.get("answer", "")).lower()
    summary = context.get("summary", {}) if isinstance(context, dict) else {}
    checked_grounded_claim = False

    # Validate average SGPA mentions
    m = re.search(r"average\s+sgpa\s+is\s+([0-9]+\.?[0-9]*)", answer_text)
    if m and "average_sgpa" in summary:
        checked_grounded_claim = True
        try:
            claimed = float(m.group(1))
            actual = float(summary.get("average_sgpa") or 0.0)
            if abs(claimed - actual) > 0.5:
                return False
        except Exception:
            return False

    # Validate failed count
    m2 = re.search(r"(\d+)\s+students?\s+failed", answer_text)
    if m2 and "failed_count" in summary:
        checked_grounded_claim = True
        try:
            claimed = int(m2.group(1))
            actual = int(summary.get("failed_count") or 0)
            if claimed != actual:
                return False
        except Exception:
            return False

    # Require explicit evidence unless all detected numeric claims matched the
    # deterministic summary context. This keeps tests and simple summary checks
    # usable while still rejecting uncited free-form claims.
    return has_citations or has_usns or checked_grounded_claim


def _is_low_quality_contextual_answer(query: str, response: Dict[str, object]) -> bool:
    answer = str(response.get("answer", "") or "").strip()
    if not answer:
        return True

    normalized_answer = _normalize_text(answer)
    normalized_query = _normalize_text(query)

    if normalized_answer == normalized_query:
        return True
    if len(normalized_answer.split()) <= 1:
        return True

    known_bad_prefixes = [
        "students with sgpa",
        "students with gp",
    ]
    return any(normalized_answer.startswith(prefix) and normalized_answer == normalized_query for prefix in known_bad_prefixes)


def _execute_contextual_answer(
    db: Session,
    query: str,
    history: Optional[Sequence[Dict[str, object]]] = None,
    retrieval_mode: str = "hybrid",
    owner_user_id: Optional[int] = None,
) -> Optional[Dict[str, object]]:
    context_start = time.perf_counter()
    use_history = _is_followup_query(query)
    effective_history = history if use_history else None
    context = _build_query_context(db, query, history=effective_history, retrieval_mode=retrieval_mode)
    context_ms = int((time.perf_counter() - context_start) * 1000)
    llm_start = time.perf_counter()
    llm_response = answer_query_from_context(query, context, history=effective_history)
    generation_ms = int((time.perf_counter() - llm_start) * 1000)
    llm_used = bool(llm_response)

    if not llm_response:
        llm_response = _deterministic_contextual_answer(query, context)
        if not llm_response:
            return None
        llm_used = False

    # If the user explicitly mentioned a student name in the query, ensure
    # the LLM-inferred student_usns are restricted to that student only.
    # This prevents contextual answers from returning other students when the
    # user asked about a specific person.
    try:
        name_candidate = _extract_name_value(query)
        if name_candidate:
            name_matched = _lookup_students_by_name_local(db, name_candidate, owner_user_id=owner_user_id)
            if name_matched:
                allowed_usns = {str(s.usn).upper() for s in name_matched}
                if llm_response.get("student_usns"):
                    llm_response["student_usns"] = [usn for usn in llm_response.get("student_usns", []) if str(usn).upper() in allowed_usns]
                if not llm_response.get("student_usns"):
                    # If LLM didn't return student_usns but a name was found, use the deterministic match
                    llm_response["student_usns"] = list(allowed_usns)
    except Exception:
        # Be conservative: if name extraction fails for any reason, continue as before
        pass
    # Also detect compacted student name mentions directly in the query (e.g. "s meenakumari")
    try:
        compact_query = "".join(query.lower().split())
        compact_matches = set()
        for student in fetch_students(db, owner_user_id=owner_user_id):
            compact_name = "".join(student.name.lower().split())
            if compact_name and compact_name in compact_query:
                compact_matches.add(str(student.usn).upper())
        if compact_matches:
            # Restrict to compact matches
            if llm_response.get("student_usns"):
                llm_response["student_usns"] = [usn for usn in llm_response.get("student_usns", []) if str(usn).upper() in compact_matches]
            if not llm_response.get("student_usns"):
                llm_response["student_usns"] = list(compact_matches)
    except Exception:
        pass

    available_usns = {
        str(student.get("usn", "")).upper()
        for student in context.get("students", [])
        if isinstance(student, dict) and student.get("usn")
    }
    if available_usns:
        llm_response["student_usns"] = [
            usn
            for usn in llm_response.get("student_usns", [])
            if str(usn).upper() in available_usns
        ]

    if not llm_response.get("student_usns"):
        inferred_usns = _infer_usns_from_matching_rows(context, limit=3)
        llm_response["student_usns"] = [
            usn for usn in inferred_usns if not available_usns or usn in available_usns
        ]

    matched_students = fetch_students_by_usns(db, llm_response.get("student_usns", []), owner_user_id=owner_user_id)
    citations = llm_response.get("citations", [])
    llm_usns = [str(usn).upper() for usn in llm_response.get("student_usns", []) if str(usn).strip()]
    context_meta = {
        "context_ms": context_ms,
        "generation_ms": generation_ms,
        "context_students": int(len(context.get("students", []))) if isinstance(context.get("students", []), list) else 0,
        "retrieved_chunks": int(len(context.get("retrieved_chunks", []))) if isinstance(context.get("retrieved_chunks", []), list) else 0,
        "matching_results": int(len(context.get("matching_results", []))) if isinstance(context.get("matching_results", []), list) else 0,
        "llm_used": llm_used,
        "retrieval_mode": retrieval_mode,
    }

    # If the user didn't explicitly request detailed grades/subjects, avoid
    # returning full per-student subject breakdowns; provide concise summaries.
    detail_keywords = re.compile(r"\b(details|detail|grades|grade|subjects|subject|report|full|complete|detailed|what grades|what is the result)\b", re.I)
    wants_details = bool(detail_keywords.search(query or ""))

    if matched_students:
        if wants_details:
            return _student_response(
                "CONTEXTUAL_ANSWER",
                llm_response["answer"],
                matched_students,
                meta={
                    "query_type": "contextual",
                    "confidence": llm_response.get("confidence", 0.0),
                    "citations": citations,
                    "student_usns": llm_usns,
                    **context_meta,
                },
            )
        # Provide concise student summaries (no subject rows)
        students_summary = [
            {"usn": s.usn, "name": s.name, "sgpa": float(s.sgpa), "pass_fail": "FAIL" if any((r.grade or "").upper() == "F" for r in (s.results or [])) else "PASS"}
            for s in matched_students
        ]
        return {
            "intent": "CONTEXTUAL_ANSWER",
            "answer": llm_response["answer"],
            "students": students_summary,
            "meta": {
                "query_type": "contextual",
                "confidence": llm_response.get("confidence", 0.0),
                "citations": citations,
                "student_usns": llm_usns,
                **context_meta,
            },
            "suggestions": [],
        }

    return {
        "intent": "CONTEXTUAL_ANSWER",
        "answer": llm_response["answer"],
        "students": [],
        "meta": {
            "query_type": "contextual",
            "confidence": llm_response.get("confidence", 0.0),
            "citations": citations,
            "student_usns": llm_usns,
            **context_meta,
        },
        "suggestions": [],
    }


def _annotate_route(
    response: Dict[str, object],
    *,
    mode_plan: Dict[str, object],
    route: str,
    intent: Optional[str] = None,
    retrieval_mode: Optional[str] = None,
    cache_hit: bool = False,
) -> Dict[str, object]:
    meta = dict(response.get("meta", {}))
    meta["planner"] = {
        "query_type": meta.get("query_type"),
        "intent": intent or response.get("intent"),
        "mode": route,
        "retrieval_mode": retrieval_mode,
        "mode_confidence": float(mode_plan.get("confidence", 0.0) or 0.0),
        "mode_reason": str(mode_plan.get("reason", "") or ""),
    }
    meta["cache_hit"] = cache_hit
    response["meta"] = meta
    return response


def _execute_structured_database_query(
    db: Session,
    query: str,
    *,
    history: Optional[Sequence[Dict[str, object]]] = None,
    mode_plan: Optional[Dict[str, object]] = None,
    dataset_ids: Optional[Sequence[int]] = None,
    merge: Optional[str] = None,
    owner_user_id: Optional[int] = None,
) -> Optional[Dict[str, object]]:
    mode_plan = mode_plan or {"mode": "sql", "confidence": 0.0, "reason": "structured"}

    # Try reverse subject lookup first (student + grade → find subjects)
    # This must run before direct_dataset_query to avoid false matches
    subject_for_grade_response = _execute_subject_for_grade_query(db, query, history=history, owner_user_id=owner_user_id)
    if subject_for_grade_response:
        return _annotate_route(subject_for_grade_response, mode_plan=mode_plan, route="sql_database", intent=subject_for_grade_response.get("intent"))

    direct_response = _execute_direct_dataset_query(db, query, dataset_ids=dataset_ids, merge=merge, owner_user_id=owner_user_id)
    if direct_response:
        return _annotate_route(direct_response, mode_plan=mode_plan, route="sql_database", intent=direct_response.get("intent"))

    subject_response = _execute_subject_result_query(db, query, history=history, owner_user_id=owner_user_id)
    if subject_response:
        return _annotate_route(subject_response, mode_plan=mode_plan, route="sql_database", intent=subject_response.get("intent"))

    generic_response = _execute_generic_grounded_filter_query(db, query, owner_user_id=owner_user_id)
    if generic_response:
        return _annotate_route(generic_response, mode_plan=mode_plan, route="sql_database", intent=generic_response.get("intent"))

    intent_result = detect_intent(query, history=history, owner_user_id=owner_user_id)
    intent = intent_result.get("intent")
    if not intent:
        return None

    intent_str = str(intent)
    confidence = float(intent_result.get("confidence", 0.0) or 0.0)
    entities = intent_result.get("entities", {})
    if not isinstance(entities, dict):
        entities = {}

    ok, reason = validate_entities_for_intent(intent_str, entities)
    if not ok:
        return _empty_response(
            f"I understood the request as {intent_str}, but the parsed values were invalid: {reason}.",
            intent=intent_str,
            meta={"query_type": _plan_query(intent_str), "validation_failed": True},
        )

    query_type = _plan_query(intent_str)
    cache_key = _cache_key(query, owner_user_id)
    if intent_str in CACHEABLE_INTENTS:
        cached = get_cached_query(cache_key)
        if cached:
            _QUERY_METRICS["cache_hits"] += 1
            return _annotate_route(cached, mode_plan=mode_plan, route="sql_database", intent=intent_str, cache_hit=True)

    if query_type == "lookup":
        response = _execute_lookup(db, query, intent_str, entities, confidence, owner_user_id=owner_user_id)
    elif query_type == "aggregation":
        response = _execute_aggregation(db, intent_str, entities, confidence, owner_user_id=owner_user_id)
    elif query_type == "filter":
        response = _execute_filter(db, intent_str, entities, confidence, owner_user_id=owner_user_id)
    else:
        return None

    response = _annotate_route(response, mode_plan=mode_plan, route="sql_database", intent=intent_str)
    if intent_str in CACHEABLE_INTENTS and response.get("students") == []:
        set_cached_query(cache_key, response)
    return response


def execute_query(db: Session, query: str, history: Optional[Sequence[Dict[str, object]]] = None, dataset_ids: Optional[Sequence[int]] = None, merge: Optional[str] = None, owner_user_id: Optional[int] = None) -> Dict[str, object]:
    _QUERY_METRICS["total_queries"] += 1

    # Basic input validation / sanitization
    sanitized = _sanitize_query(query)
    if not sanitized:
        logging.info("execute_query: rejected empty or invalid query")
        return _empty_response("Please provide a valid query.")
    query = sanitized

    students = fetch_students(db, dataset_ids=dataset_ids, merge=merge, owner_user_id=owner_user_id)
    if not students:
        return _empty_response("No student data is loaded yet. Upload a result file before querying.")

    if _is_greeting(query):
        return {
            "intent": "CHAT_GREETING",
            "answer": "Hello. Ask me anything about the uploaded student results, and I’ll answer from that dataset.",
            "students": [],
            "meta": {"query_type": "chat", "confidence": 1.0, "planner": {"query_type": "chat", "intent": "CHAT_GREETING"}, "cache_hit": False},
            "suggestions": ["topper", "result of Abir", "who failed", "Summarize this class"],
        }

    if not _is_dataset_related_query(query):
        suggestions = [*SUPPORTED_QUERY_HINTS]
        unique_suggestions = list(dict.fromkeys(suggestions))[:6]
        return _empty_response(
            "I can only answer questions about the uploaded student results.",
            suggestions=unique_suggestions,
            meta={"query_type": None},
        )

    mode_plan = plan_query_mode(query, history=history)
    retrieval_mode = str(mode_plan.get("mode", "hybrid"))

    if retrieval_mode == "sql":
        structured_response = _execute_structured_database_query(db, query, history=history, mode_plan=mode_plan, dataset_ids=dataset_ids, merge=merge, owner_user_id=owner_user_id)
        if structured_response:
            _QUERY_METRICS["structured_used"] += 1
            return structured_response

        retrieval_mode = "hybrid"
        mode_plan = {**mode_plan, "mode": "hybrid", "reason": "sql_fallback_to_hybrid"}

    if retrieval_mode == "hybrid":
        structured_response = _execute_structured_database_query(db, query, history=history, mode_plan=mode_plan, dataset_ids=dataset_ids, merge=merge, owner_user_id=owner_user_id)
        if structured_response:
            mode_plan = {**mode_plan, "structured_intent": structured_response.get("intent")}
            if str(mode_plan.get("reason", "")) in {"fallback", "sql_fallback_to_hybrid"}:
                _QUERY_METRICS["structured_used"] += 1
                return structured_response

    contextual_response = _execute_contextual_answer(db, query, history=history, retrieval_mode=retrieval_mode, owner_user_id=owner_user_id)
    contextual_confidence = float(contextual_response.get("meta", {}).get("confidence", 0.0)) if contextual_response else 0.0
    contextual_citations = bool(contextual_response and contextual_response.get("meta", {}).get("citations"))
    contextual_student_refs = bool(contextual_response and contextual_response.get("meta", {}).get("student_usns"))

    if contextual_response and _is_low_quality_contextual_answer(query, contextual_response):
        contextual_response = None
        contextual_confidence = 0.0
        contextual_citations = False
        contextual_student_refs = False

    if contextual_response and contextual_confidence >= 0.3 and (contextual_citations or contextual_student_refs):
        response_meta = dict(contextual_response.get("meta", {}))
        response_meta["planner"] = {
            "query_type": "contextual",
            "intent": "CONTEXTUAL_ANSWER",
            "mode": "vector_semantic" if retrieval_mode == "semantic" else "hybrid_database_vector",
            "retrieval_mode": retrieval_mode,
            "mode_confidence": float(mode_plan.get("confidence", 0.0) or 0.0),
            "mode_reason": str(mode_plan.get("reason", "") or ""),
        }
        response_meta["cache_hit"] = False
        contextual_response["meta"] = response_meta
        logging.info("execute_query: used hybrid RAG for query=%s, confidence=%.2f, citations=%s", query, contextual_confidence, contextual_citations)
        _QUERY_METRICS["llm_used"] += 1

        # Post-validate LLM response against DB-built context to avoid hallucinations.
        try:
            ctx = _build_query_context(db, query, history=history if _is_followup_query(query) else None)
            if not _validate_llm_response_against_context({
                "answer": contextual_response.get("answer", ""),
                "student_usns": contextual_response.get("meta", {}).get("student_usns", []),
                "citations": contextual_response.get("meta", {}).get("citations", []),
                "confidence": contextual_response.get("meta", {}).get("confidence", 0.0),
            }, ctx):
                logging.warning("Hybrid RAG answer failed grounding validation for query=%s", query)
                return _empty_response(
                    "I could not verify that answer against the uploaded dataset. Please rephrase the query with more specific student, USN, subject, grade, SGPA, or GP details.",
                    suggestions=SUPPORTED_QUERY_HINTS[:6],
                    meta={"query_type": "contextual", "validation_failed": True},
                )
        except Exception:
            logging.exception("Error validating hybrid RAG response; returning safe failure")
            return _empty_response(
                "I could not safely validate the generated answer against dataset evidence.",
                suggestions=SUPPORTED_QUERY_HINTS[:6],
                meta={"query_type": "contextual", "validation_error": True},
            )

        return contextual_response

    unique_suggestions = list(dict.fromkeys(SUPPORTED_QUERY_HINTS))[:6]
    return _empty_response(
        "I could not produce a grounded answer from the available dataset evidence for that query.",
        suggestions=unique_suggestions,
        meta={"query_type": "contextual", "grounded_answer": False},
    )
