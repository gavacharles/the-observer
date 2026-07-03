#!/usr/bin/env python3
"""Generate 300 DPI validation visualizations for SVM vs Factor Analysis models."""

from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

plt.style.use("seaborn-v0_8-whitegrid")

BASE = Path("outputs_observer_full_corpus/model_validation")
FIG = BASE / "figures_300dpi"
FIG.mkdir(parents=True, exist_ok=True)
DPI = 300


def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIG / name, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


# Load data
cmp_df = pd.read_csv(BASE / "model_comparison_summary.csv")
svm_rep = pd.read_csv(BASE / "svm_classification_report.csv", index_col=0)
fa_rep = pd.read_csv(BASE / "fa_classification_report.csv", index_col=0)
svm_cm = pd.read_csv(BASE / "svm_confusion_matrix.csv", index_col=0)
fa_cm = pd.read_csv(BASE / "fa_confusion_matrix.csv", index_col=0)
summary = json.loads((BASE / "validation_summary.json").read_text())

# Keep class rows only
excluded = {"accuracy", "macro avg", "weighted avg"}
class_rows = [i for i in svm_rep.index if i not in excluded]

# 1) Model comparison bars
metrics = ["test_accuracy", "test_f1_macro", "test_f1_weighted", "cv_f1_macro_mean"]
plot_df = cmp_df.set_index("model")[metrics]
fig, ax = plt.subplots(figsize=(10.5, 6.2))
plot_df.T.plot(kind="bar", ax=ax)
ax.set_title("Validation Metrics: SVM vs Factor-Analysis Model")
ax.set_ylabel("Score")
ax.set_xlabel("Metric")
ax.set_ylim(0, 1.0)
ax.legend(title="Model", loc="upper right")
save(fig, "validation_figure_01_model_comparison.png")

# 2) Per-class recall comparison
rec = pd.DataFrame({
    "SVM": svm_rep.loc[class_rows, "recall"],
    "FactorAnalysis": fa_rep.loc[class_rows, "recall"],
}).sort_values("SVM", ascending=False)
fig, ax = plt.subplots(figsize=(11.5, 6.5))
rec.plot(kind="bar", ax=ax)
ax.set_title("Per-class Recall Comparison")
ax.set_ylabel("Recall")
ax.set_xlabel("Class")
ax.set_ylim(0, 1.05)
ax.legend(loc="upper right")
for i, cls in enumerate(rec.index):
    ax.text(i - 0.17, rec.loc[cls, "SVM"] + 0.015, f"{rec.loc[cls, 'SVM']:.2f}", fontsize=8, rotation=90, va="bottom")
    ax.text(i + 0.02, rec.loc[cls, "FactorAnalysis"] + 0.015, f"{rec.loc[cls, 'FactorAnalysis']:.2f}", fontsize=8, rotation=90, va="bottom")
save(fig, "validation_figure_02_recall_by_class.png")

# 3) Per-class precision-recall for SVM
svm_pr = svm_rep.loc[class_rows, ["precision", "recall"]].sort_values("recall", ascending=False)
fig, ax = plt.subplots(figsize=(11.5, 6.5))
svm_pr.plot(kind="bar", ax=ax, color=["#1f77b4", "#ff7f0e"])
ax.set_title("SVM Per-class Precision vs Recall")
ax.set_ylabel("Score")
ax.set_xlabel("Class")
ax.set_ylim(0, 1.05)
ax.legend(loc="upper right")
save(fig, "validation_figure_03_svm_precision_recall.png")

# 4) Confusion matrix heatmaps (normalized)
def normalize_cm(cm_df):
    arr = cm_df.to_numpy(dtype=float)
    denom = arr.sum(axis=1, keepdims=True)
    denom[denom == 0] = 1
    return arr / denom

for cm_df, title, fname in [
    (svm_cm, "SVM Normalized Confusion Matrix", "validation_figure_04_svm_confusion_matrix.png"),
    (fa_cm, "Factor-Analysis Model Normalized Confusion Matrix", "validation_figure_05_fa_confusion_matrix.png"),
]:
    norm = normalize_cm(cm_df)
    fig, ax = plt.subplots(figsize=(8.8, 7.4))
    im = ax.imshow(norm, aspect="auto", cmap="Blues", vmin=0, vmax=1)
    ax.set_title(title)
    ax.set_xlabel("Predicted class")
    ax.set_ylabel("True class")
    ax.set_xticks(range(len(cm_df.columns)))
    ax.set_xticklabels(cm_df.columns, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(cm_df.index)))
    ax.set_yticklabels(cm_df.index, fontsize=8)
    for i in range(norm.shape[0]):
        for j in range(norm.shape[1]):
            ax.text(j, i, f"{norm[i, j]:.2f}", ha="center", va="center", fontsize=7)
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Row-normalized proportion")
    save(fig, fname)

# 5) Class support distribution (validation set source distribution)
class_counts = pd.Series(summary["class_counts"]).sort_values(ascending=False)
fig, ax = plt.subplots(figsize=(10.5, 6.0))
ax.bar(class_counts.index, class_counts.values, color="#4e79a7")
ax.set_title("Validation Subset Class Support (n=5,933)")
ax.set_xlabel("Class")
ax.set_ylabel("Sentence count")
ax.tick_params(axis="x", rotation=25)
for i, v in enumerate(class_counts.values):
    ax.text(i, v + class_counts.max() * 0.01, f"{int(v)}", ha="center", fontsize=8)
save(fig, "validation_figure_06_class_support.png")

print("Saved validation figures to:", FIG)
for p in sorted(FIG.glob("*.png")):
    print(" -", p.name)
