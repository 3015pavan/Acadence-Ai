import os
from typing import Dict, Iterable, List, Optional

import requests
from dotenv import load_dotenv

from ..models import Student


load_dotenv()

ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
INDEX_NAME = os.getenv("ELASTICSEARCH_INDEX", "students_index")


def get_elasticsearch_client() -> str:
    return ELASTICSEARCH_URL.rstrip("/")


def _request(method: str, path: str, *, client: Optional[str] = None, **kwargs):
    base_url = (client or get_elasticsearch_client()).rstrip("/")
    response = requests.request(method, f"{base_url}/{path.lstrip('/')}", timeout=kwargs.pop("timeout", 10), **kwargs)
    if response.status_code >= 400:
        response.raise_for_status()
    return response


def ensure_index(client: str) -> None:
    exists_response = requests.head(f"{client}/{INDEX_NAME}", timeout=10)
    if exists_response.status_code == 200:
        return
    if exists_response.status_code not in {200, 404}:
        exists_response.raise_for_status()

    create_response = requests.put(
        f"{client}/{INDEX_NAME}",
        json={
            "mappings": {
                "properties": {
                    "usn": {"type": "keyword"},
                    "name": {
                        "type": "text",
                        "fields": {
                            "keyword": {"type": "keyword"},
                        },
                    },
                    "sgpa": {"type": "float"},
                    "grades": {"type": "keyword"},
                    "gp": {"type": "float"},
                    "pass_fail": {"type": "keyword"},
                }
            }
        },
        timeout=10,
    )
    create_response.raise_for_status()


def sync_students(client: str, students: Iterable[Student]) -> None:
    ensure_index(client)

    delete_response = requests.post(
        f"{client}/{INDEX_NAME}/_delete_by_query",
        json={"query": {"match_all": {}}},
        timeout=30,
    )
    delete_response.raise_for_status()

    for student in students:
        index_response = requests.put(
            f"{client}/{INDEX_NAME}/_doc/{student.usn}?refresh=true",
            json={
                "usn": student.usn,
                "name": student.name,
                "sgpa": float(student.sgpa),
                "grades": [result.grade for result in student.results],
                "gp": [float(result.gp) for result in student.results if result.gp is not None],
                "pass_fail": "FAIL" if any((result.grade or "").upper() == "F" for result in student.results) else "PASS",
            },
            timeout=10,
        )
        index_response.raise_for_status()


def _extract_usns(payload: dict) -> List[str]:
    hits = payload.get("hits", {}).get("hits", [])
    return [hit.get("_source", {}).get("usn", "") for hit in hits if hit.get("_source", {}).get("usn")]


def _extract_ranked_hits(payload: dict) -> List[Dict[str, object]]:
    ranked_hits: List[Dict[str, object]] = []
    for hit in payload.get("hits", {}).get("hits", []):
        source = hit.get("_source", {})
        usn = source.get("usn")
        if not usn:
            continue
        ranked_hits.append(
            {
                "usn": usn,
                "score": float(hit.get("_score") or 0.0),
                "source": source,
            }
        )
    return ranked_hits


def search_failed_students(client: str, limit: int = 100) -> List[str]:
    response = _request(
        "POST",
        f"{INDEX_NAME}/_search",
        client=client,
        json={
            "size": limit,
            "_source": ["usn"],
            "sort": [{"sgpa": {"order": "asc"}}, {"name.keyword": {"order": "asc"}}],
            "query": {"term": {"pass_fail": "FAIL"}},
        },
        timeout=20,
    )
    return _extract_usns(response.json())


def search_students_by_grade(client: str, grade: str, limit: int = 200) -> List[str]:
    response = _request(
        "POST",
        f"{INDEX_NAME}/_search",
        client=client,
        json={
            "size": limit,
            "_source": ["usn"],
            "sort": [{"sgpa": {"order": "desc"}}],
            "query": {"term": {"grades": grade.upper()}},
        },
        timeout=20,
    )
    return _extract_usns(response.json())


def search_students_by_name(client: str, name: str, limit: int = 20) -> List[str]:
    response = _request(
        "POST",
        f"{INDEX_NAME}/_search",
        client=client,
        json={
            "size": limit,
            "_source": ["usn"],
            "sort": [{"_score": {"order": "desc"}}, {"sgpa": {"order": "desc"}}],
            "query": {
                "bool": {
                    "should": [
                        {"match_phrase": {"name": {"query": name, "boost": 4}}},
                        {"match_phrase_prefix": {"name": {"query": name, "boost": 3}}},
                        {"match": {"name": {"query": name, "operator": "and"}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
        },
        timeout=20,
    )
    return _extract_usns(response.json())


def search_students_by_name_ranked(client: str, name: str, limit: int = 20) -> List[Dict[str, object]]:
    response = _request(
        "POST",
        f"{INDEX_NAME}/_search",
        client=client,
        json={
            "size": limit,
            "_source": ["usn", "name", "sgpa", "pass_fail", "grades"],
            "query": {
                "bool": {
                    "should": [
                        {"match_phrase": {"name": {"query": name, "boost": 5}}},
                        {"match_phrase_prefix": {"name": {"query": name, "boost": 4}}},
                        {"match": {"name": {"query": name, "operator": "and", "boost": 3}}},
                        {"match": {"name": {"query": name, "operator": "or"}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
        },
        timeout=20,
    )
    return _extract_ranked_hits(response.json())


def search_students_by_name_prefix(client: str, prefix: str, limit: int = 100) -> List[str]:
    response = _request(
        "POST",
        f"{INDEX_NAME}/_search",
        client=client,
        json={
            "size": limit,
            "_source": ["usn"],
            "sort": [{"name.keyword": {"order": "asc"}}],
            "query": {"match_phrase_prefix": {"name": prefix}},
        },
        timeout=20,
    )
    return _extract_usns(response.json())


def search_students_by_usn_prefix(client: str, prefix: str, limit: int = 100) -> List[str]:
    response = _request(
        "POST",
        f"{INDEX_NAME}/_search",
        client=client,
        json={
            "size": limit,
            "_source": ["usn"],
            "sort": [{"usn": {"order": "asc"}}],
            "query": {"prefix": {"usn": prefix.upper()}},
        },
        timeout=20,
    )
    return _extract_usns(response.json())


def search_students_by_grade_and_failure(client: str, grade: str, limit: int = 200) -> List[str]:
    response = _request(
        "POST",
        f"{INDEX_NAME}/_search",
        client=client,
        json={
            "size": limit,
            "_source": ["usn"],
            "query": {
                "bool": {
                    "must": [
                        {"term": {"grades": grade.upper()}},
                        {"term": {"pass_fail": "FAIL"}},
                    ]
                }
            },
        },
        timeout=20,
    )
    return _extract_usns(response.json())


def search_students_with_gp_zero(client: str, limit: int = 200) -> List[str]:
    response = _request(
        "POST",
        f"{INDEX_NAME}/_search",
        client=client,
        json={
            "size": limit,
            "_source": ["usn"],
            "query": {"term": {"gp": 0.0}},
        },
        timeout=20,
    )
    return _extract_usns(response.json())
