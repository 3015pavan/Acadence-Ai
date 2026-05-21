import pytest

from backend.services.intelligence import (
    _extract_usn_value,
    _extract_name_value,
    _extract_grade_value,
    _extract_subject_phrase_intel,
)


def test_extract_usn_value():
    assert _extract_usn_value("show 1MS21CS001") == "1MS21CS001"
    assert _extract_usn_value("student 2RV20EC123") == "2RV20EC123"
    assert _extract_usn_value("no usn here") is None


def test_extract_name_value():
    assert _extract_name_value("result of Abir") == "abir"
    assert _extract_name_value("What is the SGPA of Meena Kumari?") == "meena kumari"
    assert _extract_name_value('"Ananya" result') == "ananya"


def test_extract_grade_value():
    assert _extract_grade_value("students with a+") == "A+"
    assert _extract_grade_value("who got f in chemistry") == "F"
    assert _extract_grade_value("no grade present") is None


def test_extract_subject_phrase_intel():
    assert _extract_subject_phrase_intel("students with A+ in design thinking") == "design thinking"
    assert _extract_subject_phrase_intel("who failed in engineering chemistry lab") == "engineering chemistry lab"
    assert _extract_subject_phrase_intel("no subject here") is None
