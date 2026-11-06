import numpy as np
import pandas as pd

from docai_lakehouse import router as R


def test_router_beats_threshold_when_confidence_is_uninformative():
    rng = np.random.default_rng(0)
    n = 600
    layout = rng.choice(["a", "b", "c"], n)
    bad = (layout == "c") & (rng.random(n) < 0.6)          # errors depend on layout, not on confidence
    ev = pd.DataFrame({"outcome": np.where(bad, "wrong", "correct"), "confidence": rng.uniform(0.95, 1.0, n),
                       "rule_flag": rng.random(n) < 0.05, "scanned": rng.random(n) < 0.5,
                       "value": rng.uniform(1e3, 1e6, n), "method": "rules", "field": "f", "layout": layout,
                       "carrier_id": rng.choice(["C1", "C2", "C3", "C4"], n)})
    cv = R.cross_validate(R.features(ev), threshold=0.97)
    assert cv["silent_errors_router_policy"] < cv["silent_errors_threshold_policy"]
    assert cv["auc_router"] > 0.8 > cv["auc_confidence_only"]
