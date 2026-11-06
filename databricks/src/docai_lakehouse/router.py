"""Review router: a model that predicts which extracted values are wrong (AI model build on Databricks).

Baseline policy: send a value to a person if confidence < threshold OR a validity rule fires.
Router policy: send the same NUMBER of values, but choose them by predicted risk.
Hybrid policy: validity-rule hits always, then the rest of the same budget by predicted risk.
Same review budget → fewer silent errors means the router routes better.

Validation is leave-one-carrier-out, so the router is always scored on a carrier (layout + wording) it
didn't train on, the same discipline as the extraction benchmark.
"""
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

FEATURES_NUM = ["confidence", "rule_flag", "scanned", "log_value"]
FEATURES_CAT = ["method", "field", "layout"]


def features(ev: pd.DataFrame) -> pd.DataFrame:
    x = ev[ev.outcome.isin(["correct", "wrong", "spurious"])].copy()
    x["confidence"] = x["confidence"].astype(float).fillna(1.0)
    x["rule_flag"] = x["rule_flag"].astype(bool).astype(int)
    x["scanned"] = x["scanned"].astype(bool).astype(int)
    x["log_value"] = np.log10(x["value"].astype(float).clip(lower=1))
    x["bad"] = x["outcome"].isin(["wrong", "spurious"]).astype(int)
    return x.reset_index(drop=True)


def make_model() -> Pipeline:
    pre = ColumnTransformer([("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), FEATURES_CAT)], remainder="passthrough")
    clf = HistGradientBoostingClassifier(max_depth=3, max_iter=150, learning_rate=0.1, random_state=0)
    return Pipeline([("pre", pre), ("clf", clf)])


def cross_validate(x: pd.DataFrame, threshold: float) -> dict:
    """Out-of-fold risk scores by leave-one-carrier-out; compare policies at equal review budget."""
    cols = FEATURES_CAT + FEATURES_NUM
    risk = np.full(len(x), np.nan)
    for carrier in sorted(x.carrier_id.unique()):
        test = x.carrier_id == carrier
        train = ~test
        if x.loc[train, "bad"].nunique() < 2:
            continue
        m = make_model().fit(x.loc[train, cols], x.loc[train, "bad"])
        risk[test.values] = m.predict_proba(x.loc[test, cols])[:, 1]
    x = x.assign(risk=risk).dropna(subset=["risk"])
    base_flag = (x.rule_flag == 1) | (x.confidence < threshold)
    budget = int(base_flag.sum())
    order = np.argsort(-x.risk.values)
    router_flag = np.zeros(len(x), dtype=bool)
    router_flag[order[:budget]] = True
    # hybrid: validity-rule hits always go to review, the rest of the same budget goes by predicted risk
    rule = (x.rule_flag == 1).values
    hybrid_flag = rule.copy()
    remaining = max(budget - int(rule.sum()), 0)
    rest = [i for i in order if not rule[i]][:remaining]
    hybrid_flag[rest] = True
    bad = x.bad.values.astype(bool)
    return {
        "values": int(len(x)), "errors": int(bad.sum()), "review_budget": budget,
        "review_rate": budget / len(x),
        "silent_errors_threshold_policy": int((bad & ~base_flag.values).sum()),
        "silent_errors_router_policy": int((bad & ~router_flag).sum()),
        "silent_errors_hybrid_policy": int((bad & ~hybrid_flag).sum()),
        "auc_router": float(roc_auc_score(bad, x.risk)) if 0 < bad.sum() < len(bad) else float("nan"),
        "auc_confidence_only": float(roc_auc_score(bad, -x.confidence)) if 0 < bad.sum() < len(bad) else float("nan"),
    }


def fit_final(x: pd.DataFrame) -> Pipeline:
    return make_model().fit(x[FEATURES_CAT + FEATURES_NUM], x["bad"])
