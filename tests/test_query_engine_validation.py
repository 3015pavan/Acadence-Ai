from backend.services.intelligence import validate_entities_for_intent
from backend.services.query_engine import _validate_llm_response_against_context


def test_validate_entities_sgpa_range_ok():
    ok, reason = validate_entities_for_intent("GET_SGPA_RANGE", {"min_sgpa": 5.0, "max_sgpa": 8.0})
    assert ok


def test_validate_entities_sgpa_range_bad():
    ok, reason = validate_entities_for_intent("GET_SGPA_RANGE", {"min_sgpa": 9.0, "max_sgpa": 5.0})
    assert not ok


def test_validate_entities_limit_bad():
    ok, reason = validate_entities_for_intent("GET_TOP_N", {"limit": 1000})
    assert not ok


def test_llm_validator_agrees_with_summary():
    ctx = {"summary": {"average_sgpa": 6.5, "failed_count": 3}}
    resp = {"answer": "The average SGPA is 6.5 and 3 students failed."}
    assert _validate_llm_response_against_context(resp, ctx)


def test_llm_validator_rejects_bad_claim():
    ctx = {"summary": {"average_sgpa": 6.5, "failed_count": 3}}
    resp = {"answer": "The average SGPA is 8.9 and 5 students failed."}
    assert not _validate_llm_response_against_context(resp, ctx)
