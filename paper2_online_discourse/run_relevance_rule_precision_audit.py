#!/usr/bin/env python3
"""Targeted precision audit for `land_row_dispute` construction nexus."""

from pathlib import Path
import json
import re
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "outputs_observer_full_corpus"
OUT = BASE / "model_validation"
OUT.mkdir(parents=True, exist_ok=True)

RANDOM_STATE = 42
N_AUDIT = 200
LAND_SHARE = 0.626  # from Table 2 headline proportion

NEXUS_RE = re.compile(
    r"\b(construction|construct|infrastructure|project|contractor|contract|road|roads|highway|bridge|expressway|bypass|street|dam|hydropower|pipeline|eacop|rail|railway|airport|housing|building|school|hospital|power|energy|water|sewer|drain|works?|kcca|unra|nwsc|uegcl|ministry of works)\b",
    flags=re.IGNORECASE,
)


def wilson_ci(k: int, n: int, z: float = 1.96):
    if n <= 0:
        return float("nan"), float("nan")
    p = k / n
    d = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / d
    half = (z * np.sqrt((p * (1.0 - p) / n) + (z * z / (4.0 * n * n)))) / d
    return float(center - half), float(center + half)


def main():
    sent = pd.read_csv(BASE / "sentences_classified.csv")
    land = sent[sent["pred_final"] == "land_row_dispute"].dropna(subset=["sentence"]).copy()

    if len(land) < N_AUDIT:
        raise ValueError(f"Not enough land_row_dispute rows for audit: {len(land)}")

    sample = land.sample(n=N_AUDIT, random_state=RANDOM_STATE).copy()
    sample = sample.reset_index(drop=True)
    sample["audit_id"] = [f"LR_AUDIT_{i+1:03d}" for i in range(len(sample))]

    context_text = (
        sample["title"].fillna("")
        + ". "
        + sample["sentence"].fillna("")
        + " "
        + sample["url"].fillna("")
    )

    sample["construction_nexus"] = context_text.apply(lambda t: bool(NEXUS_RE.search(str(t))))

    k = int(sample["construction_nexus"].sum())
    n = int(len(sample))
    p_hat = k / n
    lo, hi = wilson_ci(k, n)

    adjusted_point = LAND_SHARE * p_hat
    adjusted_lo = LAND_SHARE * lo
    adjusted_hi = LAND_SHARE * hi

    # Save audit sample and summary
    out_cols = [
        "audit_id",
        "url",
        "publication_date",
        "title",
        "sentence",
        "construction_nexus",
        "pred_final",
        "conf_final",
        "fusion_rule",
    ]
    sample[out_cols].to_csv(OUT / "relevance_rule_precision_audit_sample.csv", index=False)

    summary = {
        "audit": "land_row_dispute_construction_nexus_precision",
        "sample_size": n,
        "random_state": RANDOM_STATE,
        "true_construction_nexus": k,
        "precision_estimate": p_hat,
        "ci_95_wilson": [lo, hi],
        "classifier_precision_land_row_dispute": 0.9510869565217391,
        "headline_land_share_raw": LAND_SHARE,
        "headline_land_share_adjusted_point": adjusted_point,
        "headline_land_share_adjusted_ci_95": [adjusted_lo, adjusted_hi],
        "interpretation": "If precision is materially below classifier precision, report adjusted range alongside raw 62.6% headline share.",
    }

    with open(OUT / "relevance_rule_precision_audit_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
