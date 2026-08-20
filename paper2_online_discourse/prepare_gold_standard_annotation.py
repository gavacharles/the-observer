#!/usr/bin/env python3
"""Prepare a blinded stratified gold-standard annotation sample."""

from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "outputs_observer_full_corpus"
OUT = BASE / "gold_standard"
OUT.mkdir(parents=True, exist_ok=True)

SEED = 42
TARGET_PER_CATEGORY = 100  # 7 categories => n=700


def main():
    sent = pd.read_csv(BASE / "sentences_classified.csv")
    sent = sent.dropna(subset=["sentence", "pred_final"]).copy()

    # Keep only taxonomy labels present in pipeline outputs
    cats = sorted(sent["pred_final"].dropna().unique().tolist())

    # Build deterministic sample_id
    sent = sent.reset_index(drop=False).rename(columns={"index": "row_id"})
    sent["sample_id"] = sent["row_id"].apply(lambda x: f"OBS_GS_{int(x):06d}")

    sampled_parts = []
    sample_counts = {}
    for c in cats:
        grp = sent[sent["pred_final"] == c]
        n_take = min(TARGET_PER_CATEGORY, len(grp))
        sampled = grp.sample(n=n_take, random_state=SEED)
        sampled_parts.append(sampled)
        sample_counts[c] = int(n_take)

    sample = pd.concat(sampled_parts, ignore_index=True)
    sample = sample.sample(frac=1.0, random_state=SEED).reset_index(drop=True)

    # Keep complete key (contains pseudo-label and model outputs)
    key_cols = [
        "sample_id", "source", "url", "publication_date", "title", "sentence",
        "pred_final", "pred_content", "pred_semantic", "fusion_rule", "conf_final",
    ]
    key_df = sample[key_cols].copy()
    key_df.to_csv(OUT / "gold_sample_key.csv", index=False)

    # Blinded files for coders (no model predictions shown)
    blind_cols = ["sample_id", "source", "publication_date", "title", "sentence"]
    coder_template = sample[blind_cols].copy()
    coder_template["coder_label"] = ""
    coder_template["coder_notes"] = ""

    coder_template.to_csv(OUT / "gold_sample_coder1_template.csv", index=False)
    coder_template.to_csv(OUT / "gold_sample_coder2_template.csv", index=False)

    # Adjudication sheet
    adjud_cols = blind_cols.copy()
    adjud_df = sample[adjud_cols].copy()
    adjud_df["coder1_label"] = ""
    adjud_df["coder2_label"] = ""
    adjud_df["adjudicated_label"] = ""
    adjud_df["adjudicator_notes"] = ""
    adjud_df.to_csv(OUT / "gold_sample_adjudication_template.csv", index=False)

    manifest = {
        "seed": SEED,
        "target_per_category": TARGET_PER_CATEGORY,
        "final_sample_size": int(len(sample)),
        "categories": cats,
        "sample_counts": sample_counts,
        "files": {
            "key": "gold_sample_key.csv",
            "coder1": "gold_sample_coder1_template.csv",
            "coder2": "gold_sample_coder2_template.csv",
            "adjudication": "gold_sample_adjudication_template.csv",
        },
        "note": "Coder templates are blinded to model predictions.",
    }

    with open(OUT / "gold_sample_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
