#!/usr/bin/env python3
"""Generate 300 DPI visualizations for Observer full-corpus NLP results."""

from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

plt.style.use("seaborn-v0_8-whitegrid")

BASE = Path("outputs_observer_full_corpus")
FIG_DIR = BASE / "figures_300dpi"
FIG_DIR.mkdir(parents=True, exist_ok=True)

DPI = 300


def save(fig, name: str):
    fig.tight_layout()
    fig.savefig(FIG_DIR / name, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


# Load datasets
sent = pd.read_csv(BASE / "sentences_classified.csv")
yearly = pd.read_csv(BASE / "table_yearly_summary.csv")
cat_dist = pd.read_csv(BASE / "table_category_distribution.csv")
fusion = pd.read_csv(BASE / "table_fusion_rules.csv")
yc = pd.read_csv(BASE / "table_year_category_counts.csv")

# Normalize yearly type
yearly["year"] = yearly["year"].astype(int)

# 1) Yearly totals: downloaded, relevant articles, relevant sentences
fig, ax1 = plt.subplots(figsize=(11, 6.5))
ax1.plot(yearly["year"], yearly["downloaded_articles"], marker="o", linewidth=2.2, label="Downloaded articles", color="#1f77b4")
ax1.plot(yearly["year"], yearly["relevant_articles"], marker="o", linewidth=2.2, label="Relevant articles", color="#2ca02c")
ax1.set_xlabel("Year")
ax1.set_ylabel("Articles")
ax1.set_title("Observer Corpus Yield by Year (2016–2025)")

ax2 = ax1.twinx()
ax2.plot(yearly["year"], yearly["relevant_sentences"], marker="s", linewidth=2.2, label="Relevant sentences", color="#d62728")
ax2.set_ylabel("Relevant sentences")

lines, labels = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines + lines2, labels + labels2, loc="upper right", frameon=True)
save(fig, "figure_01_yearly_corpus_yield.png")


# 2) Category distribution bar chart
fig, ax = plt.subplots(figsize=(10.5, 6.5))
plot_df = cat_dist.sort_values("sentence_count", ascending=False).copy()
ax.barh(plot_df["category"], plot_df["sentence_count"], color="#4e79a7")
ax.invert_yaxis()
ax.set_title("Dispute Category Distribution (Relevant Sentences)")
ax.set_xlabel("Sentence count")
for i, (v, pct) in enumerate(zip(plot_df["sentence_count"], plot_df["share_pct"])):
    ax.text(v + max(plot_df["sentence_count"]) * 0.01, i, f"{pct:.1f}%", va="center", fontsize=9)
save(fig, "figure_02_category_distribution.png")


# 3) Stacked yearly category composition
# table_year_category_counts has first column as year and remaining as categories
yc_cols = [c for c in yc.columns if c != "year"]
yc["year"] = yc["year"].astype(int)
yc_sorted = yc.sort_values("year")

fig, ax = plt.subplots(figsize=(12, 7))
bottom = pd.Series([0] * len(yc_sorted))
colors = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc949", "#af7aa1"]
for idx, col in enumerate(yc_cols):
    ax.bar(yc_sorted["year"], yc_sorted[col], bottom=bottom, label=col, color=colors[idx % len(colors)])
    bottom += yc_sorted[col]

ax.set_title("Yearly Category Composition of Relevant Sentences")
ax.set_xlabel("Year")
ax.set_ylabel("Sentence count")
ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=True)
save(fig, "figure_03_yearly_category_stacked.png")


# 4) Monthly heatmap (year x month)
sent["date"] = pd.to_datetime(sent["publication_date"], errors="coerce")
sent = sent.dropna(subset=["date"]).copy()
sent["year"] = sent["date"].dt.year
sent["month"] = sent["date"].dt.month
heat = sent.pivot_table(index="year", columns="month", values="sentence", aggfunc="count", fill_value=0)

fig, ax = plt.subplots(figsize=(12, 6.8))
im = ax.imshow(heat.values, aspect="auto")
ax.set_title("Monthly Intensity of Relevant Sentences (Observer)")
ax.set_xlabel("Month")
ax.set_ylabel("Year")
ax.set_xticks(range(12))
ax.set_xticklabels(["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"])
ax.set_yticks(range(len(heat.index)))
ax.set_yticklabels(heat.index.astype(str))
cbar = fig.colorbar(im, ax=ax)
cbar.set_label("Relevant sentences")
save(fig, "figure_04_monthly_intensity_heatmap.png")


# 5) Fusion rule distribution
fig, ax = plt.subplots(figsize=(8.5, 5.8))
f = fusion.sort_values("count", ascending=False)
ax.bar(f["fusion_rule"], f["count"], color=["#1f77b4", "#2ca02c", "#ff7f0e"])
ax.set_title("Fusion Rule Outcomes")
ax.set_xlabel("Fusion rule")
ax.set_ylabel("Sentence count")
for i, (count, pct) in enumerate(zip(f["count"], f["share_pct"])):
    ax.text(i, count + max(f["count"]) * 0.02, f"{pct:.1f}%", ha="center", fontsize=9)
save(fig, "figure_05_fusion_rule_distribution.png")


# 6) Relevance rate by year
fig, ax = plt.subplots(figsize=(10.5, 6.0))
rate = (yearly["relevant_articles"] / yearly["downloaded_articles"] * 100).round(2)
ax.plot(yearly["year"], rate, marker="o", linewidth=2.5, color="#9467bd")
ax.fill_between(yearly["year"], rate, alpha=0.15, color="#9467bd")
ax.set_title("Relevant Article Rate by Year")
ax.set_xlabel("Year")
ax.set_ylabel("Relevance rate (%)")
ax.set_ylim(bottom=0)
for x, y in zip(yearly["year"], rate):
    ax.text(x, y + 0.7, f"{y:.1f}%", ha="center", fontsize=8)
save(fig, "figure_06_relevance_rate_by_year.png")

print("Saved figures to:", FIG_DIR)
for p in sorted(FIG_DIR.glob("*.png")):
    print(" -", p.name)
