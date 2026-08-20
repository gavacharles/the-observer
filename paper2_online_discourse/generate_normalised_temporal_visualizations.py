#!/usr/bin/env python3
"""Generate normalised temporal analysis visualisations (300 DPI)."""

from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

plt.style.use("seaborn-v0_8-whitegrid")

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "outputs_observer_full_corpus" / "newspaper_only_analysis"
FIG = BASE / "figures_300dpi"
FIG.mkdir(parents=True, exist_ok=True)
DPI = 300

NICE = {
    "land_row_dispute": "Land / RoW",
    "delay_time_overrun": "Delay / Time",
    "procurement_irregularity": "Procurement",
    "quality_technical_defect": "Quality",
    "governance_oversight_failure": "Governance",
    "contract_management_failure": "Contract",
    "payment_financial_dispute": "Payment",
}


def save(fig, name: str):
    fig.tight_layout()
    fig.savefig(FIG / name, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


def main():
    trends = pd.read_csv(BASE / "driver_trends_normalised_per_1000_articles_2016_2025.csv")
    stats = pd.read_csv(BASE / "driver_trend_statistics.csv")

    cats = [c for c in trends.columns if c not in ["year", "total_articles"]]
    order = [
        "land_row_dispute",
        "delay_time_overrun",
        "procurement_irregularity",
        "quality_technical_defect",
        "governance_oversight_failure",
        "contract_management_failure",
        "payment_financial_dispute",
    ]
    cats = [c for c in order if c in cats]

    # 1) Normalised annual time series
    fig, ax = plt.subplots(figsize=(12, 7))
    for c in cats:
        ax.plot(trends["year"], trends[c], marker="o", linewidth=2, label=NICE.get(c, c))
    ax.set_title("Normalised Annual Dispute Trends (per 1,000 sitemap articles)")
    ax.set_xlabel("Year")
    ax.set_ylabel("Rate per 1,000 articles")
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=True)
    save(fig, "news_figure_14_normalised_trends.png")

    # 2) Normalised slope by category (Holm-significant highlighted)
    plot_df = stats.copy()
    plot_df["nice"] = plot_df["driver"].map(NICE).fillna(plot_df["driver"])
    plot_df = plot_df.sort_values("slope_normalised_per_1000_articles_per_year", ascending=True)
    sig = plot_df["p_value_holm"] < 0.10
    colors = np.where(sig, "#2ca02c", "#9e9e9e")

    fig, ax = plt.subplots(figsize=(10.5, 6.3))
    ax.barh(plot_df["nice"], plot_df["slope_normalised_per_1000_articles_per_year"], color=colors)
    ax.set_title("OLS Slope on Normalised Annual Series")
    ax.set_xlabel("Slope (rate per 1,000 articles per year)")
    ax.axvline(0, color="black", linewidth=1)
    save(fig, "news_figure_15_normalised_slopes_holm.png")

    # 3) Raw vs Holm-adjusted p-values
    plot_df2 = stats.copy()
    plot_df2["nice"] = plot_df2["driver"].map(NICE).fillna(plot_df2["driver"])
    plot_df2 = plot_df2.sort_values("p_value_raw", ascending=True)

    y = np.arange(len(plot_df2))
    fig, ax = plt.subplots(figsize=(10.8, 6.2))
    ax.hlines(y=y, xmin=plot_df2["p_value_raw"], xmax=plot_df2["p_value_holm"], color="#bdbdbd", linewidth=2)
    ax.scatter(plot_df2["p_value_raw"], y, color="#1f77b4", label="Raw p", zorder=3)
    ax.scatter(plot_df2["p_value_holm"], y, color="#d62728", label="Holm p", zorder=3)
    ax.axvline(0.10, color="black", linestyle="--", linewidth=1, label="0.10 threshold")
    ax.set_yticks(y)
    ax.set_yticklabels(plot_df2["nice"])
    ax.set_xlabel("p-value")
    ax.set_title("Raw vs Holm-adjusted p-values by Category")
    ax.legend(loc="lower right", frameon=True)
    save(fig, "news_figure_16_pvalues_raw_vs_holm.png")

    # 4) Durbin-Watson diagnostic
    plot_df3 = stats.copy()
    plot_df3["nice"] = plot_df3["driver"].map(NICE).fillna(plot_df3["driver"])
    plot_df3 = plot_df3.sort_values("durbin_watson", ascending=True)

    fig, ax = plt.subplots(figsize=(10.5, 6.2))
    ax.barh(plot_df3["nice"], plot_df3["durbin_watson"], color="#4e79a7")
    ax.axvline(2.0, color="black", linestyle="--", linewidth=1, label="DW = 2")
    ax.set_xlabel("Durbin-Watson statistic")
    ax.set_title("Residual Autocorrelation Diagnostic (DW)")
    ax.legend(frameon=True, loc="lower right")
    save(fig, "news_figure_17_durbin_watson.png")

    print("Saved figures:")
    for name in [
        "news_figure_14_normalised_trends.png",
        "news_figure_15_normalised_slopes_holm.png",
        "news_figure_16_pvalues_raw_vs_holm.png",
        "news_figure_17_durbin_watson.png",
    ]:
        print(str(FIG / name))


if __name__ == "__main__":
    main()
