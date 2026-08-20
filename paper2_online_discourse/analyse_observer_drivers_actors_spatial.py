#!/usr/bin/env python3
"""Observer-only driver, actor, money, and spatial analysis with 300 DPI figures."""

from pathlib import Path
import json
import re
import itertools
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import networkx as nx
from scipy.stats import linregress

plt.style.use("seaborn-v0_8-whitegrid")

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "outputs_observer_full_corpus"
OUT = BASE / "newspaper_only_analysis"
FIG = OUT / "figures_300dpi"
OUT.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)
DPI = 300


def save(fig, name: str):
    fig.tight_layout()
    fig.savefig(FIG / name, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


def short_label(x: str, max_len: int = 55) -> str:
    x = str(x)
    return x if len(x) <= max_len else x[: max_len - 1] + "…"


ACTOR_PATTERNS = {
    "KCCA": [r"\bKCCA\b", r"\bKampala Capital City Authority\b"],
    "UNRA": [r"\bUNRA\b", r"\bUnra\b", r"\bUganda National Roads Authority\b"],
    "Parliament": [r"\bparliament\b", r"\bparliamentary commission\b"],
    "Government": [r"\bgovernment\b", r"\bGovt\b", r"\bstate\b"],
    "President Museveni": [r"\bPresident Museveni\b", r"\bMuseveni\b"],
    "Ministry of Works": [r"\bMinistry of Works\b", r"\bworks ministry\b"],
    "Ministry of Finance": [r"\bMinistry of Finance\b", r"\bfinance ministry\b"],
    "Ministry of Energy": [r"\bMinistry of Energy\b", r"\benergy ministry\b"],
    "Ministry of Health": [r"\bMinistry of Health\b", r"\bhealth ministry\b"],
    "Ministry of Education": [r"\bMinistry of Education\b", r"\beducation ministry\b"],
    "World Bank": [r"\bWorld Bank\b"],
    "IGG": [r"\bIGG\b", r"\bInspector General of Government\b"],
    "PPDA": [r"\bPPDA\b", r"\bPublic Procurement and Disposal of Public Assets\b"],
    "NEMA": [r"\bNEMA\b", r"\bNational Environment Management Authority\b"],
    "NWSC": [r"\bNWSC\b", r"\bNational Water and Sewerage Corporation\b"],
    "Umeme": [r"\bUmeme\b"],
    "UEGCL": [r"\bUEGCL\b", r"\bUganda Electricity Generation Company\b"],
    "URA": [r"\bURA\b", r"\bUganda Revenue Authority\b"],
    "NSSF": [r"\bNSSF\b", r"\bNational Social Security Fund\b"],
    "Police": [r"\bpolice\b"],
    "Court": [r"\bcourt\b", r"\bjudge\b", r"\bjustice\b"],
    "EACOP": [r"\bEACOP\b", r"\bEast African Crude Oil Pipeline\b"],
    "Umeme/ERA": [r"\bERA\b", r"\bElectricity Regulatory Authority\b", r"\bUmeme\b"],
}

LOCATION_REGION = {
    "Kampala": "Central", "Entebbe": "Central", "Mukono": "Central", "Nakawa": "Central", "Lubowa": "Central",
    "Wakiso": "Central", "Namboole": "Central", "Nakivubo": "Central",
    "Jinja": "Eastern", "Mbale": "Eastern", "Soroti": "Eastern", "Tororo": "Eastern", "Karamoja": "Northern/Eastern",
    "Gulu": "Northern", "Kitgum": "Northern", "Arua": "Northern", "Lira": "Northern",
    "Hoima": "Western", "Kabale": "Western", "Kabaale": "Western", "Mubende": "Central/Western", "Bunyoro": "Western",
    "Karuma": "Northern", "Isimba": "Eastern", "Bujagali": "Eastern", "Mubende": "Central/Western",
    "Nile": "National Corridor", "South Sudan": "Cross-border", "DRC": "Cross-border", "Kenya": "Cross-border", "Tanzania": "Cross-border",
}

MONEY_RE = re.compile(
    r"\b(?:Shs|UGX|UShs?)\s*([0-9]+(?:\.[0-9]+)?)\s*(trillion|billion|million|bn|m|tn)?\b",
    flags=re.IGNORECASE,
)

SCALE = {
    None: 1,
    "million": 1_000_000,
    "m": 1_000_000,
    "billion": 1_000_000_000,
    "bn": 1_000_000_000,
    "trillion": 1_000_000_000_000,
    "tn": 1_000_000_000_000,
}


def compile_patterns(d):
    return {k: [re.compile(p, flags=re.IGNORECASE) for p in v] for k, v in d.items()}


def detect_matches(text, compiled):
    hits = []
    for name, pats in compiled.items():
        if any(p.search(text) for p in pats):
            hits.append(name)
    return sorted(set(hits))


def parse_money(text):
    vals = []
    for num, scale in MONEY_RE.findall(str(text)):
        try:
            vals.append(float(num) * SCALE.get((scale or "").lower() or None, 1))
        except Exception:
            continue
    return vals


def classify_trend(slope, pvalue):
    if np.isnan(slope):
        return "uncertain"
    if pvalue < 0.10 and slope > 0:
        return "increasing"
    if pvalue < 0.10 and slope < 0:
        return "decreasing"
    return "stable/mixed"


def holm_bonferroni(pvalues: np.ndarray) -> np.ndarray:
    """Holm-Bonferroni adjusted p-values."""
    pvalues = np.asarray(pvalues, dtype=float)
    m = len(pvalues)
    order = np.argsort(pvalues)
    adjusted = np.empty(m, dtype=float)

    for rank, idx in enumerate(order, start=1):
        adjusted[idx] = min(1.0, (m - rank + 1) * pvalues[idx])

    monotone = adjusted[order].copy()
    for i in range(1, m):
        if monotone[i] < monotone[i - 1]:
            monotone[i] = monotone[i - 1]
    adjusted[order] = monotone
    return adjusted


def durbin_watson(residuals: np.ndarray) -> float:
    den = float(np.sum(np.square(residuals)))
    if den <= 0:
        return float("nan")
    num = float(np.sum(np.square(np.diff(residuals))))
    return num / den


def main():
    sent = pd.read_csv(BASE / "sentences_classified.csv")
    arts = pd.read_csv(BASE / "articles_relevant.csv")
    sent["year"] = pd.to_datetime(sent["publication_date"], errors="coerce").dt.year
    arts["year"] = pd.to_datetime(arts["publication_date"], errors="coerce").dt.year
    sent = sent[(sent["year"] >= 2016) & (sent["year"] <= 2025)].copy()
    arts = arts[(arts["year"] >= 2016) & (arts["year"] <= 2025)].copy()

    # Driver trends
    yearly_driver = (
        sent.pivot_table(index="year", columns="pred_final", values="sentence", aggfunc="count", fill_value=0)
        .sort_index()
    )
    yearly_driver.to_csv(OUT / "driver_trends_yearly_2016_2025.csv")

    # Normalize by yearly publication volume from sitemap:
    # R(c, y) = 1000 * N(c, y) / A(y)
    all_urls = pd.read_csv(BASE / "all_sitemap_urls.csv")
    all_urls["year"] = pd.to_datetime(all_urls["date"], errors="coerce").dt.year
    article_volume = (
        all_urls[(all_urls["year"] >= 2016) & (all_urls["year"] <= 2025)]
        .groupby("year")
        .size()
        .rename("total_articles")
        .reset_index()
        .sort_values("year")
    )

    yearly_driver_norm = yearly_driver.reset_index().merge(article_volume, on="year", how="left")
    for col in yearly_driver.columns:
        yearly_driver_norm[col] = 1000.0 * yearly_driver_norm[col] / yearly_driver_norm["total_articles"]
    yearly_driver_norm.to_csv(OUT / "driver_trends_normalised_per_1000_articles_2016_2025.csv", index=False)

    trend_rows = []
    pvals_raw = []
    x = yearly_driver.index.to_numpy(dtype=float)
    for col in yearly_driver.columns:
        y_raw = yearly_driver[col].to_numpy(dtype=float)
        y_norm = yearly_driver_norm[col].to_numpy(dtype=float)
        lr = linregress(x, y_norm)
        y_hat = lr.intercept + lr.slope * x
        resid = y_norm - y_hat
        first = float(y_raw[0]) if len(y_raw) else np.nan
        last = float(y_raw[-1]) if len(y_raw) else np.nan
        pct_change = ((last - first) / first * 100.0) if first not in (0, np.nan) and first != 0 else np.nan

        pvals_raw.append(float(lr.pvalue))
        trend_rows.append({
            "driver": col,
            "count_total": int(y_raw.sum()),
            "count_2016": int(first),
            "count_2025": int(last),
            "slope_normalised_per_1000_articles_per_year": float(lr.slope),
            "r_value": float(lr.rvalue),
            "p_value_raw": float(lr.pvalue),
            "durbin_watson": float(durbin_watson(resid)),
            "pct_change_2016_to_2025": float(pct_change) if not np.isnan(pct_change) else np.nan,
        })

    pvals_holm = holm_bonferroni(np.array(pvals_raw, dtype=float))
    for i, row in enumerate(trend_rows):
        row["p_value_holm"] = float(pvals_holm[i])
        row["trend_label"] = classify_trend(
            row["slope_normalised_per_1000_articles_per_year"],
            row["p_value_holm"],
        )

    trend_df = pd.DataFrame(trend_rows).sort_values(["count_total"], ascending=False)
    trend_df.to_csv(OUT / "driver_trend_statistics.csv", index=False)

    # Actor extraction
    compiled_actors = compile_patterns(ACTOR_PATTERNS)
    arts["actor_hits"] = (arts["title"].fillna("") + ". " + arts["text"].fillna("")).apply(lambda t: detect_matches(t, compiled_actors))

    actor_counts = Counter()
    for actors in arts["actor_hits"]:
        actor_counts.update(actors)
    actor_df = pd.DataFrame(actor_counts.items(), columns=["actor", "article_mentions"]).sort_values("article_mentions", ascending=False)
    actor_df.to_csv(OUT / "actor_mentions_top.csv", index=False)

    # Actor network
    edge_counter = Counter()
    for actors in arts["actor_hits"]:
        for a, b in itertools.combinations(sorted(set(actors)), 2):
            edge_counter[(a, b)] += 1
    edge_df = pd.DataFrame([{"source": a, "target": b, "weight": w} for (a, b), w in edge_counter.items()])
    if not edge_df.empty:
        edge_df = edge_df.sort_values("weight", ascending=False)
    edge_df.to_csv(OUT / "actor_network_edges.csv", index=False)
    G = nx.Graph()
    for _, r in edge_df.iterrows():
        G.add_edge(r["source"], r["target"], weight=float(r["weight"]))
    node_rows = []
    if len(G.nodes) > 0:
        deg = nx.degree_centrality(G)
        btw = nx.betweenness_centrality(G, weight="weight")
        eig = nx.eigenvector_centrality_numpy(G, weight="weight") if len(G.nodes) > 1 else {n: 0.0 for n in G.nodes}
        for n in G.nodes:
            node_rows.append({
                "actor": n,
                "degree_centrality": deg[n],
                "betweenness_centrality": btw[n],
                "eigenvector_centrality": eig[n],
            })
    node_df = pd.DataFrame(node_rows).sort_values("eigenvector_centrality", ascending=False)
    node_df.to_csv(OUT / "actor_network_nodes.csv", index=False)

    # Money analysis
    sent["money_values"] = (sent["title"].fillna("") + ". " + sent["sentence"].fillna("")).apply(parse_money)
    money_rows = []
    for _, r in sent.iterrows():
        for val in r["money_values"]:
            money_rows.append({
                "year": int(r["year"]),
                "pred_final": r["pred_final"],
                "amount_ugx": float(val),
                "url": r["url"],
                "title": r["title"],
                "sentence": r["sentence"],
            })
    money_df = pd.DataFrame(money_rows)
    if money_df.empty:
        money_df = pd.DataFrame(columns=["year", "pred_final", "amount_ugx", "url", "title", "sentence"])
    money_df.to_csv(OUT / "money_mentions_detailed.csv", index=False)
    if not money_df.empty:
        money_year = money_df.groupby("year")["amount_ugx"].agg(["count", "sum", "median", "max"]).reset_index()
        money_cat = money_df.groupby("pred_final")["amount_ugx"].agg(["count", "sum", "median", "max"]).reset_index()
        top_money = money_df.sort_values("amount_ugx", ascending=False).head(100)
    else:
        money_year = pd.DataFrame(columns=["year", "count", "sum", "median", "max"])
        money_cat = pd.DataFrame(columns=["pred_final", "count", "sum", "median", "max"])
        top_money = money_df.copy()
    money_year.to_csv(OUT / "money_mentions_by_year.csv", index=False)
    money_cat.to_csv(OUT / "money_mentions_by_driver.csv", index=False)
    top_money.to_csv(OUT / "top_money_mentions.csv", index=False)

    # Spatial mapping
    compiled_locs = {loc: re.compile(rf"\b{re.escape(loc)}\b", flags=re.IGNORECASE) for loc in LOCATION_REGION}
    arts["location_hits"] = (arts["title"].fillna("") + ". " + arts["text"].fillna("")).apply(
        lambda t: sorted({loc for loc, pat in compiled_locs.items() if pat.search(t)})
    )
    loc_counter = Counter()
    region_year_counter = defaultdict(int)
    for _, r in arts.iterrows():
        year = int(r["year"])
        for loc in r["location_hits"]:
            loc_counter[loc] += 1
            region_year_counter[(LOCATION_REGION[loc], year)] += 1
    loc_df = pd.DataFrame(loc_counter.items(), columns=["location", "article_mentions"]).sort_values("article_mentions", ascending=False)
    loc_df["region"] = loc_df["location"].map(LOCATION_REGION)
    loc_df.to_csv(OUT / "location_mentions_top.csv", index=False)
    region_year_df = pd.DataFrame([
        {"region": reg, "year": yr, "count": c} for (reg, yr), c in region_year_counter.items()
    ])
    if not region_year_df.empty:
        region_pivot = region_year_df.pivot(index="region", columns="year", values="count").fillna(0).astype(int)
    else:
        region_pivot = pd.DataFrame()
    region_pivot.to_csv(OUT / "region_year_heatmap_table.csv")

    # FIGURES
    # 1 driver trends line
    fig, ax = plt.subplots(figsize=(12, 7))
    for col in yearly_driver.columns:
        ax.plot(yearly_driver.index, yearly_driver[col], marker="o", linewidth=2, label=col)
    ax.set_title("Observer Newspaper Driver Trends, 2016–2025")
    ax.set_xlabel("Year")
    ax.set_ylabel("Relevant sentence count")
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=True)
    save(fig, "news_figure_01_driver_trends.png")

    # 2 driver slopes
    fig, ax = plt.subplots(figsize=(10, 6))
    tmp = trend_df.sort_values("slope_normalised_per_1000_articles_per_year", ascending=False)
    colors = [
        "#2ca02c" if x > 0 else "#d62728" if x < 0 else "#7f7f7f"
        for x in tmp["slope_normalised_per_1000_articles_per_year"]
    ]
    ax.barh(tmp["driver"], tmp["slope_normalised_per_1000_articles_per_year"], color=colors)
    ax.set_title("Direction of Newspaper Driver Change (Normalised, 2016–2025)")
    ax.set_xlabel("Linear slope per year (sentences per 1,000 sitemap articles)")
    save(fig, "news_figure_02_driver_slopes.png")

    # 3 top actors
    fig, ax = plt.subplots(figsize=(10.5, 6.2))
    topa = actor_df.head(12).sort_values("article_mentions")
    ax.barh(topa["actor"], topa["article_mentions"], color="#4e79a7")
    ax.set_title("Top Actors in Observer Infrastructure-Risk Coverage")
    ax.set_xlabel("Article mentions")
    save(fig, "news_figure_03_top_actors.png")

    # 4 actor network
    fig, ax = plt.subplots(figsize=(10, 8))
    if len(G.nodes) > 0:
        pos = nx.spring_layout(G, seed=42, weight="weight")
        maxw = max([G[u][v]["weight"] for u, v in G.edges]) if len(G.edges) else 1
        widths = [0.7 + 5 * (G[u][v]["weight"] / maxw) for u, v in G.edges]
        eig_map = {r['actor']: r['eigenvector_centrality'] for _, r in node_df.iterrows()} if not node_df.empty else {n:1 for n in G.nodes}
        sizes = [1500 + 9000 * eig_map.get(n, 0) for n in G.nodes]
        nx.draw_networkx_edges(G, pos, width=widths, alpha=0.4, ax=ax)
        nx.draw_networkx_nodes(G, pos, node_size=sizes, node_color="#2a9d8f", ax=ax)
        nx.draw_networkx_labels(G, pos, font_size=9, ax=ax)
        ax.axis("off")
    ax.set_title("Actor Co-occurrence Network in Observer Risk Articles")
    save(fig, "news_figure_04_actor_network.png")

    # 5 money by year
    fig, ax = plt.subplots(figsize=(10.5, 6.2))
    if not money_year.empty:
        ax.bar(money_year["year"], money_year["sum"] / 1e9, color="#f28e2b")
    ax.set_title("Total Mentioned Money by Year in Risk Sentences")
    ax.set_xlabel("Year")
    ax.set_ylabel("Nominal mentioned amount (UGX billions)")
    save(fig, "news_figure_05_money_by_year.png")

    # 6 region heatmap
    fig, ax = plt.subplots(figsize=(10, 6.5))
    if not region_pivot.empty:
        sns.heatmap(region_pivot, cmap="YlGnBu", annot=True, fmt="d", ax=ax)
    ax.set_title("Spatial Distribution of Mentioned Regions in Observer Risk Articles")
    save(fig, "news_figure_06_region_heatmap.png")

    # 7 money mention count + median by year
    fig, ax1 = plt.subplots(figsize=(10.5, 6.2))
    if not money_year.empty:
        ax1.bar(money_year["year"], money_year["count"], color="#76b7b2", alpha=0.8, label="Mentions")
        ax1.set_ylabel("Money mentions (count)")
        ax2 = ax1.twinx()
        ax2.plot(money_year["year"], money_year["median"] / 1e9, color="#e15759", marker="o", linewidth=2.3, label="Median")
        ax2.set_ylabel("Median amount (UGX billions)")
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")
    ax1.set_title("Money Mentions: Frequency and Median Value by Year")
    ax1.set_xlabel("Year")
    save(fig, "news_figure_07_money_mentions_count_median.png")

    # 8 money by driver (count and median)
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    if not money_cat.empty:
        mcount = money_cat.sort_values("count", ascending=True)
        axes[0].barh(mcount["pred_final"], mcount["count"], color="#59a14f")
        axes[0].set_title("Money Mentions by Driver (Count)")
        axes[0].set_xlabel("Count")

        mmed = money_cat.sort_values("median", ascending=True)
        axes[1].barh(mmed["pred_final"], mmed["median"] / 1e9, color="#edc949")
        axes[1].set_title("Money Mentions by Driver (Median)")
        axes[1].set_xlabel("Median mentioned amount (UGX billions)")
    save(fig, "news_figure_08_money_by_driver.png")

    # 9 top locations
    fig, ax = plt.subplots(figsize=(10.5, 6.4))
    if not loc_df.empty:
        toploc = loc_df.head(15).sort_values("article_mentions")
        ax.barh(toploc["location"], toploc["article_mentions"], color="#4e79a7")
    ax.set_title("Top Mentioned Locations in Observer Risk Articles")
    ax.set_xlabel("Article mentions")
    save(fig, "news_figure_09_top_locations.png")

    # 10 actor centrality heatmap
    fig, ax = plt.subplots(figsize=(10.8, 6.2))
    if not node_df.empty:
        cent = node_df.head(15).set_index("actor")[["degree_centrality", "betweenness_centrality", "eigenvector_centrality"]]
        sns.heatmap(cent, cmap="viridis", annot=True, fmt=".3f", ax=ax)
    ax.set_title("Actor Centrality Metrics (Top Actors)")
    save(fig, "news_figure_10_actor_centrality_heatmap.png")

    # 11 strongest actor co-occurrence edges
    fig, ax = plt.subplots(figsize=(11, 6.5))
    if not edge_df.empty:
        e = edge_df.head(15).copy()
        e["pair"] = e["source"] + " ↔ " + e["target"]
        e = e.sort_values("weight", ascending=True)
        ax.barh(e["pair"], e["weight"], color="#af7aa1")
    ax.set_title("Strongest Actor Co-occurrence Links")
    ax.set_xlabel("Co-mention count")
    save(fig, "news_figure_11_top_actor_edges.png")

    # 12 top monetary references
    fig, ax = plt.subplots(figsize=(12.5, 7.0))
    if not top_money.empty:
        tm = top_money.head(12).copy().iloc[::-1]
        tm["label"] = tm["title"].apply(short_label)
        ax.barh(tm["label"], tm["amount_ugx"] / 1e12, color="#f28e2b")
    ax.set_title("Largest Monetary References in Risk Sentences")
    ax.set_xlabel("Mentioned amount (UGX trillions)")
    save(fig, "news_figure_12_top_money_mentions.png")

    # 13 driver share distribution
    fig, ax = plt.subplots(figsize=(8.5, 8.5))
    d = trend_df.sort_values("count_total", ascending=False)
    if not d.empty:
        ax.pie(d["count_total"], labels=d["driver"], autopct="%1.1f%%", startangle=120, pctdistance=0.78)
        centre = plt.Circle((0, 0), 0.55, fc="white")
        ax.add_artist(centre)
    ax.set_title("Share of Total Risk Sentences by Driver")
    save(fig, "news_figure_13_driver_share_donut.png")

    summary = {
        "period": "2016-2025",
        "relevant_sentences": int(len(sent)),
        "relevant_articles": int(len(arts)),
        "top_drivers": trend_df.head(5).to_dict(orient="records"),
        "top_actors": actor_df.head(10).to_dict(orient="records"),
        "top_locations": loc_df.head(10).to_dict(orient="records"),
        "money_mentions_count": int(len(money_df)),
        "total_mentioned_money_ugx": float(money_df["amount_ugx"].sum()) if not money_df.empty else 0.0,
    }
    with open(OUT / "newspaper_only_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
