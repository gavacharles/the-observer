#!/usr/bin/env python3
"""Compute gold-standard reliability and agreement metrics after manual coding."""

from pathlib import Path
import json
import numpy as np
import pandas as pd
from sklearn.metrics import cohen_kappa_score, accuracy_score, classification_report

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "outputs_observer_full_corpus"
GS = BASE / "gold_standard"
OUT = BASE / "model_validation"
OUT.mkdir(parents=True, exist_ok=True)


def _norm_label(x):
    if pd.isna(x):
        return np.nan
    return str(x).strip()


def main():
    key = pd.read_csv(GS / "gold_sample_key.csv")
    c1 = pd.read_csv(GS / "gold_sample_coder1_template.csv")
    c2 = pd.read_csv(GS / "gold_sample_coder2_template.csv")
    adj = pd.read_csv(GS / "gold_sample_adjudication_template.csv")

    c1 = c1[["sample_id", "coder_label"]].rename(columns={"coder_label": "coder1_label"})
    c2 = c2[["sample_id", "coder_label"]].rename(columns={"coder_label": "coder2_label"})
    adj = adj[["sample_id", "adjudicated_label"]]

    df = key.merge(c1, on="sample_id", how="left").merge(c2, on="sample_id", how="left").merge(adj, on="sample_id", how="left")

    for col in ["pred_final", "coder1_label", "coder2_label", "adjudicated_label"]:
        df[col] = df[col].apply(_norm_label)

    usable = df.dropna(subset=["coder1_label", "coder2_label", "adjudicated_label"]).copy()
    if usable.empty:
        raise ValueError("No completed annotations found. Fill coder and adjudication files first.")

    # Overall Cohen's kappa between coder 1 and coder 2
    kappa_overall = float(cohen_kappa_score(usable["coder1_label"], usable["coder2_label"]))

    # Per-category kappa (one-vs-rest)
    cats = sorted(usable["adjudicated_label"].dropna().unique().tolist())
    kappa_rows = []
    for c in cats:
        y1 = (usable["coder1_label"] == c).astype(int)
        y2 = (usable["coder2_label"] == c).astype(int)
        k = float(cohen_kappa_score(y1, y2))
        kappa_rows.append({"category": c, "cohen_kappa": k})
    kappa_df = pd.DataFrame(kappa_rows)

    # Number of disagreements requiring adjudication
    disagreements = int((usable["coder1_label"] != usable["coder2_label"]).sum())

    # Pseudo-label vs gold-standard agreement
    pseudo_acc = float(accuracy_score(usable["adjudicated_label"], usable["pred_final"]))
    pseudo_kappa = float(cohen_kappa_score(usable["adjudicated_label"], usable["pred_final"]))

    # Optional full model report vs gold
    rep = classification_report(
        usable["adjudicated_label"],
        usable["pred_final"],
        output_dict=True,
        zero_division=0,
    )
    rep_df = pd.DataFrame(rep).T
    rep_df.to_csv(OUT / "gold_standard_vs_pseudo_classification_report.csv")

    kappa_df.to_csv(OUT / "gold_standard_kappa_by_category.csv", index=False)

    summary = {
        "gold_standard_sample_size": int(len(usable)),
        "cohen_kappa_overall": kappa_overall,
        "cohen_kappa_by_category": {r["category"]: float(r["cohen_kappa"]) for _, r in kappa_df.iterrows()},
        "adjudicated_disagreements": disagreements,
        "pseudo_vs_gold": {
            "accuracy": pseudo_acc,
            "cohen_kappa": pseudo_kappa,
        },
        "outputs": {
            "kappa_by_category": "gold_standard_kappa_by_category.csv",
            "pseudo_vs_gold_report": "gold_standard_vs_pseudo_classification_report.csv",
        },
    }

    with open(OUT / "gold_standard_validation_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
