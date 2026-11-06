"""Review feedback logic: reviewer agreement with the answer key, and reason grouping."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from build_review_feedback import agrees, primary_reason  # noqa: E402


def test_agreement_matches_answer_key():
    assert agrees("accepted", "correct") is True          # kept a right value
    assert agrees("accepted", "wrong") is False           # let a wrong value through
    assert agrees("corrected", "wrong") is True
    assert agrees("not_on_document", "spurious") is True  # rejected a made-up value
    assert agrees("not_on_document", "correct") is False
    assert agrees("open", "wrong") is None                # no decision yet → not scored


def test_primary_reason():
    assert primary_reason("low_confidence") == "low confidence"
    assert primary_reason("low_confidence;out_of_range") == "validity rule"
    assert primary_reason("ratio_vs_coverage_a_dwelling") == "validity rule"
