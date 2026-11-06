"""Parsing + mapping rules, using label/value shapes seen on real documents (no real values)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from normalize import match_field, norm_label, parse, review  # noqa: E402


def test_label_prefixes_stripped():
    assert norm_label("A. Dwelling") == "dwelling"
    assert norm_label("Wind / Hail:") == "wind hail"


def test_synonyms_map_to_canonical_fields():
    assert match_field(norm_label("Separate Structures")) == "coverage_b_other_structures"
    assert match_field(norm_label("Additional Living Expenses")) == "coverage_d_loss_of_use"
    assert match_field(norm_label("E. Personal Liability - Each Occurrence")) == "coverage_e_liability"
    assert match_field(norm_label("Guest Medical Protection")) == "coverage_f_medical"
    assert match_field(norm_label("EST. ANNUAL PREMIUM")) == "premium_annual_total"


def test_money_and_percent():
    assert parse("money", "$470,693.00")[0] == 470693.0
    assert parse("money", "$1,722.43")[0] == 1722.43
    assert parse("percent", "2% - $10480")[0] == 2.0


def test_int_handles_thousands_and_skips_dollar_pieces():
    assert parse("int", "2,529")[0] == 2529
    assert parse("int", "$1,878\n2021")[0] == 2021   # DI once merged a premium into the year-built value


def test_review_rules():
    vals = {"coverage_a_dwelling": 470000}
    assert "ratio_vs_coverage_a_dwelling" in review("coverage_d_loss_of_use", 12.0, 0.99, "quote", vals, 1)
    assert "low_confidence" in review("coverage_a_dwelling", 470000, 0.5, "quote", vals, 1)
    assert review("premium_annual_total", None, None, "quote", vals, 0) == ["missing_required"]
    assert review("coverage_a_dwelling", 470000, 0.99, "quote", vals, 1) == []


def test_money_skips_percent():
    assert parse("money", "1% ($2,687)")[0] == 2687.0


def test_redact_label_anchored_ids():
    from redact import ID_LABEL
    text = "Policy Number: TXZ1234567\nLoan #0123456789 Escrow: No\nReference# AB1234567\nPremium $1,234"
    found = [m.group(2) for m in ID_LABEL.finditer(text)]
    assert found == ["TXZ1234567", "0123456789", "AB1234567"]


def test_training_label_locator_prefers_value_near_its_label():
    from build_training_set import locate
    page = {"words": [{"content": "$300,000", "polygon": [5, 1, 6, 1, 6, 1.2, 5, 1.2]},
                      {"content": "$300,000", "polygon": [5, 3, 6, 3, 6, 3.2, 5, 3.2]}],
            "lines": [{"content": "Family Liability", "polygon": [1, 3, 3, 3, 3, 3.2, 1, 3.2]}]}
    assert locate(page, "coverage_e_liability", 300000, "Family Liability")["polygon"][1] == 3
