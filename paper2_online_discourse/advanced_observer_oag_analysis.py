#!/usr/bin/env python3
"""Network, correlation, and lagged-causality analysis for Observer vs OAG risk signals."""

from pathlib import Path
import json
import itertools
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import networkx as nx
from scipy.stats import pearsonr, f as f_dist

plt.style.use("seaborn-v0_8-whitegrid")

ROOT = Path(__file__).resolve().parent
OBS_DIR = ROOT / "outputs_observer_full_corpus"
OUT = OBS_DIR / "integration_analysis"
FIG = OUT / "figures_300dpi"
OUT.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)
DPI = 300


def save(fig, name: str):
    fig.tight_layout()
    fig.savefig(FIG / name, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


def ols_sse(y: np.ndarray, X: np.ndarray):
    beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    sse = float(np.sum(resid ** 2))
    return sse, beta


def granger_like_test(y: np.ndarray, x: np.ndarray):
    """Lag-1 test: y_t ~ y_{t-1} + x_{t-1} vs y_t ~ y_{t-1}."""
    if len(y) < 5:
        return np.nan, np.nan, np.nan

    yt = y[1:]
    y_lag = y[:-1]
    x_lag = x[:-1]

    Xr = np.column_stack([np.ones(len(yt)), y_lag])
    Xu = np.column_stack([np.ones(len(yt)), y_lag, x_lag])

    sse_r, _ = ols_sse(yt, Xr)
    sse_u, beta_u = ols_sse(yt, Xu)

    df1 = Xu.shape[1] - Xr.shape[1]  # 1
    df2 = len(yt) - Xu.shape[1]
    if df2 <= 0 or sse_u <= 0:
        return np.nan, np.nan, np.nan

    F = ((sse_r - sse_u) / df1) / (sse_u / df2)
    p = 1 - f_dist.cdf(F, df1, df2)
    return float(F), float(p), float(beta_u[-1])


def main():
    # -------------------------
    # Load datasets
    # -------------------------
    sent = pd.read_csv(OBS_DIR / "sentences_classified.csv")
    sent["year"] = pd.to_datetime(sent["publication_date"], errors="coerce").dt.year

    oag = pd.read_csv(ROOT.parent / "government_Auditor_General" / "driver_trend_by_year_2017_2025.csv")

    # Keep overlap period for integration
    sent = sent[(sent["year"] >= 2018) & (sent["year"] <= 2025)].copy()
    oag = oag[(oag["year"] >= 2018) & (oag["year"] <= 2025)].copy()

    # -------------------------
    # Step A: yearly newspaper category series
    # -------------------------
    obs_year_cat = (
        sent.groupby(["year", "pred_final"]).size()
        .reset_index(name="count")
        .pivot(index="year", columns="pred_final", values="count")
        .fillna(0)
        .astype(int)
        .sort_index()
    )
    obs_year_cat.to_csv(OUT / "observer_year_category_counts_2018_2025.csv")

    # -------------------------
    # Step B: map categories to OAG series
    # -------------------------
    mapped = pd.DataFrame(index=obs_year_cat.index)
    mapped["obs_procurement_irregularity"] = obs_year_cat.get("procurement_irregularity", 0)
    mapped["obs_contract_management_failure"] = obs_year_cat.get("contract_management_failure", 0)
    mapped["obs_governance_oversight_failure"] = obs_year_cat.get("governance_oversight_failure", 0)
    mapped["obs_land_row_dispute"] = obs_year_cat.get("land_row_dispute", 0)
    mapped["obs_payment_financial_dispute"] = obs_year_cat.get("payment_financial_dispute", 0)

    oag_idx = oag.set_index("year").sort_index()
    mapped["oag_procurement_irregularities"] = oag_idx["procurement_irregularities"]
    mapped["oag_contract_management"] = oag_idx["contract_management"]
    mapped["oag_governance_and_controls"] = oag_idx["governance_and_controls"]
    mapped["oag_land_and_right_of_way"] = oag_idx["land_and_right_of_way"]
    mapped["oag_financial_claims_payments"] = oag_idx["claims_and_liabilities"] + oag_idx["delayed_payments"]

    mapped.to_csv(OUT / "observer_oag_mapped_series_2018_2025.csv")

    # -------------------------
    # Step C: correlation analysis
    # -------------------------
    pairs = [
        ("procurement", "obs_procurement_irregularity", "oag_procurement_irregularities"),
        ("contract_management", "obs_contract_management_failure", "oag_contract_management"),
        ("governance", "obs_governance_oversight_failure", "oag_governance_and_controls"),
        ("land", "obs_land_row_dispute", "oag_land_and_right_of_way"),
        ("financial", "obs_payment_financial_dispute", "oag_financial_claims_payments"),
    ]

    corr_rows = []
    for name, ox, oy in pairs:
        x = mapped[ox].to_numpy(dtype=float)
        y = mapped[oy].to_numpy(dtype=float)

        r_now, p_now = pearsonr(x, y)

        # Lag correlation: newspaper(t-1) vs OAG(t)
        if len(x) > 2:
            r_lag, p_lag = pearsonr(x[:-1], y[1:])
        else:
            r_lag, p_lag = np.nan, np.nan

        corr_rows.append({
            "mapped_domain": name,
            "pearson_r_same_year": float(r_now),
            "p_value_same_year": float(p_now),
            "pearson_r_news_lag1_to_oag": float(r_lag),
            "p_value_news_lag1_to_oag": float(p_lag),
        })

    corr_df = pd.DataFrame(corr_rows)
    corr_df.to_csv(OUT / "correlation_results.csv", index=False)

    # -------------------------
    # Step D: Granger-like lag-1 test
    # -------------------------
    caus_rows = []
    for name, ox, oy in pairs:
        y = mapped[oy].to_numpy(dtype=float)
        x = mapped[ox].to_numpy(dtype=float)
        F, p, beta_lag_news = granger_like_test(y, x)
        caus_rows.append({
            "mapped_domain": name,
            "F_stat_lag1": F,
            "p_value_lag1": p,
            "beta_news_lag1": beta_lag_news,
            "n_years": int(len(x)),
            "note": "Exploratory lag-1 test on annual series; low power due to n=8",
        })

    caus_df = pd.DataFrame(caus_rows)
    caus_df.to_csv(OUT / "causality_lag1_results.csv", index=False)

    # -------------------------
    # Step E: category co-occurrence network (within article)
    # -------------------------
    by_url = sent.groupby("url")["pred_final"].apply(lambda s: sorted(set(s.dropna()))).reset_index(name="cats")
    edge_w = {}
    for cats in by_url["cats"]:
        if len(cats) < 2:
            continue
        for a, b in itertools.combinations(cats, 2):
            key = tuple(sorted((a, b)))
            edge_w[key] = edge_w.get(key, 0) + 1

    edges = pd.DataFrame([
        {"source": a, "target": b, "weight": w}
        for (a, b), w in edge_w.items()
    ]).sort_values("weight", ascending=False)
    edges.to_csv(OUT / "category_network_edges.csv", index=False)

    G = nx.Graph()
    for _, r in edges.iterrows():
        G.add_edge(r["source"], r["target"], weight=float(r["weight"]))

    deg = nx.degree_centrality(G)
    btw = nx.betweenness_centrality(G, weight="weight")
    eig = nx.eigenvector_centrality_numpy(G, weight="weight") if len(G.nodes) > 1 else {n: 0 for n in G.nodes}

    nodes = pd.DataFrame({
        "node": list(G.nodes),
        "degree_centrality": [deg[n] for n in G.nodes],
        "betweenness_centrality": [btw[n] for n in G.nodes],
        "eigenvector_centrality": [eig[n] for n in G.nodes],
    }).sort_values("eigenvector_centrality", ascending=False)
    nodes.to_csv(OUT / "category_network_nodes.csv", index=False)

    # -------------------------
    # Figures
    # -------------------------
    # F1 correlation heatmap on mapped series
    corr_mat = mapped.corr(numeric_only=True)
    fig, ax = plt.subplots(figsize=(10.5, 8.5))
    sns.heatmap(corr_mat, cmap="coolwarm", center=0, annot=True, fmt=".2f", ax=ax)
    ax.set_title("Observer–OAG Mapped Series Correlation Matrix (2018–2025)")
    save(fig, "integration_figure_01_correlation_heatmap.png")

    # F2 lag correlation bar chart
    fig, ax = plt.subplots(figsize=(9.5, 5.8))
    tmp = corr_df.sort_values("pearson_r_news_lag1_to_oag", ascending=False)
    ax.bar(tmp["mapped_domain"], tmp["pearson_r_news_lag1_to_oag"], color="#4e79a7")
    ax.axhline(0, color="black", linewidth=1)
    ax.set_ylabel("Pearson r")
    ax.set_title("Lag Correlation: Newspaper(t-1) vs OAG(t)")
    for i, v in enumerate(tmp["pearson_r_news_lag1_to_oag"]):
        ax.text(i, v + (0.02 if v >= 0 else -0.06), f"{v:.2f}", ha="center", fontsize=9)
    save(fig, "integration_figure_02_lag_correlation.png")

    # F3 network plot
    fig, ax = plt.subplots(figsize=(9, 7))
    if len(G.nodes) > 0:
        pos = nx.spring_layout(G, seed=42, weight="weight")
        widths = [0.5 + 5 * (G[u][v]["weight"] / edges["weight"].max()) for u, v in G.edges]
        node_sizes = [8000 * eig[n] + 1200 for n in G.nodes]
        nx.draw_networkx_edges(G, pos, width=widths, alpha=0.5, ax=ax)
        nx.draw_networkx_nodes(G, pos, node_size=node_sizes, node_color="#2a9d8f", alpha=0.9, ax=ax)
        nx.draw_networkx_labels(G, pos, font_size=9, ax=ax)
        ax.set_title("Category Co-occurrence Network (Article-level)")
        ax.axis("off")
    save(fig, "integration_figure_03_category_network.png")

    # F4 model comparison (copied context in integration section)
    cmp = pd.read_csv(OBS_DIR / "model_validation" / "model_comparison_summary.csv")
    m = cmp.set_index("model")[["test_accuracy", "test_f1_macro", "cv_f1_macro_mean"]]
    fig, ax = plt.subplots(figsize=(9.5, 5.8))
    m.T.plot(kind="bar", ax=ax)
    ax.set_ylim(0, 1)
    ax.set_ylabel("Score")
    ax.set_title("Validation Snapshot: SVM vs Factor-Analysis Classifier")
    save(fig, "integration_figure_04_validation_snapshot.png")

    summary = {
        "period": "2018-2025",
        "n_observer_sentences": int(len(sent)),
        "n_oag_years": int(len(oag)),
        "correlation_table": corr_rows,
        "causality_table": caus_rows,
        "network_nodes": int(len(nodes)),
        "network_edges": int(len(edges)),
    }
    with open(OUT / "integration_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("Integration analysis complete:", OUT)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
