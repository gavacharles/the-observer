#!/usr/bin/env python3
"""
Extended dispute analysis for Observer corpus.
Produces:
  - Actor-type classification and sector tagging
  - Dispute co-occurrence heatmap within articles
  - Cramér's V correlation matrix (driver × region, driver × actor-type)
  - Actor community detection (Louvain)
  - Temporal series with election/event annotations + change-point analysis
  - Tripartite actor–dispute–region linkage table
  - Category co-occurrence counts matrix
  - All 300 DPI figures
"""
from pathlib import Path
import json, re, itertools, warnings
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns
import networkx as nx
from scipy.stats import chi2_contingency, pearsonr
from scipy.signal import find_peaks
from sklearn.preprocessing import LabelEncoder

warnings.filterwarnings("ignore")
plt.style.use("seaborn-v0_8-whitegrid")

ROOT   = Path(__file__).resolve().parent
BASE   = ROOT / "outputs_observer_full_corpus"
NPONLY = BASE / "newspaper_only_analysis"
EXTOUT = BASE / "extended_analysis"
FIG    = EXTOUT / "figures_300dpi"
EXTOUT.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)
DPI = 300

NICE_NAMES = {
    "land_row_dispute":           "Land / RoW Dispute",
    "delay_time_overrun":         "Delay / Time Overrun",
    "procurement_irregularity":   "Procurement Irregularity",
    "quality_technical_defect":   "Quality / Technical Defect",
    "governance_oversight_failure":"Governance / Oversight Failure",
    "contract_management_failure":"Contract Management Failure",
    "payment_financial_dispute":  "Payment / Financial Dispute",
}

# ── Actor-type taxonomy ──────────────────────────────────────────────────────
ACTOR_TYPE = {
    "Government":           "State Executive",
    "President Museveni":   "State Executive",
    "Ministry of Finance":  "State Executive",
    "Ministry of Works":    "State Executive",
    "Ministry of Energy":   "State Executive",
    "Ministry of Health":   "State Executive",
    "Ministry of Education":"State Executive",
    "KCCA":                 "State Executive",
    "Parliament":           "Legislature",
    "Court":                "Judiciary",
    "Police":               "Coercive State",
    "IGG":                  "Oversight Body",
    "PPDA":                 "Oversight Body",
    "NEMA":                 "Oversight Body",
    "URA":                  "Oversight Body",
    "UNRA":                 "Infrastructure Agency",
    "NWSC":                 "Infrastructure Agency",
    "UEGCL":                "Infrastructure Agency",
    "Umeme":                "Utility / Infrastructure",
    "Umeme/ERA":            "Utility / Infrastructure",
    "NSSF":                 "Financial Institution",
    "World Bank":           "International / Donor",
    "EACOP":                "Private / Commercial",
}

LOCATION_REGION = {
    "Kampala":"Central","Entebbe":"Central","Mukono":"Central","Nakawa":"Central",
    "Lubowa":"Central","Wakiso":"Central","Namboole":"Central","Nakivubo":"Central",
    "Jinja":"Eastern","Mbale":"Eastern","Soroti":"Eastern","Tororo":"Eastern",
    "Karamoja":"Northern/Eastern",
    "Gulu":"Northern","Kitgum":"Northern","Arua":"Northern","Lira":"Northern",
    "Hoima":"Western","Kabale":"Western","Kabaale":"Western","Bunyoro":"Western",
    "Mubende":"Central/Western",
    "Karuma":"Northern","Isimba":"Eastern","Bujagali":"Eastern",
    "Nile":"National Corridor",
    "South Sudan":"Cross-border","DRC":"Cross-border",
    "Kenya":"Cross-border","Tanzania":"Cross-border",
}

ACTOR_PATTERNS = {
    "KCCA":              [r"\bKCCA\b",r"\bKampala Capital City Authority\b"],
    "UNRA":              [r"\bUNRA\b",r"\bUganda National Roads Authority\b"],
    "Parliament":        [r"\bparliament\b",r"\bparliamentary\b"],
    "Government":        [r"\bgovernment\b",r"\bGovt\b"],
    "President Museveni":[r"\bPresident Museveni\b",r"\bMuseveni\b"],
    "Ministry of Works": [r"\bMinistry of Works\b",r"\bworks ministry\b"],
    "Ministry of Finance":[r"\bMinistry of Finance\b",r"\bfinance ministry\b"],
    "Ministry of Energy":[r"\bMinistry of Energy\b",r"\benergy ministry\b"],
    "Ministry of Health":[r"\bMinistry of Health\b",r"\bhealth ministry\b"],
    "Ministry of Education":[r"\bMinistry of Education\b",r"\beducation ministry\b"],
    "World Bank":        [r"\bWorld Bank\b"],
    "IGG":               [r"\bIGG\b",r"\bInspector General of Government\b"],
    "PPDA":              [r"\bPPDA\b"],
    "NEMA":              [r"\bNEMA\b"],
    "NWSC":              [r"\bNWSC\b"],
    "Umeme":             [r"\bUmeme\b"],
    "UEGCL":             [r"\bUEGCL\b"],
    "URA":               [r"\bURA\b",r"\bUganda Revenue Authority\b"],
    "NSSF":              [r"\bNSSF\b"],
    "Police":            [r"\bpolice\b"],
    "Court":             [r"\bcourt\b",r"\bjudge\b",r"\bjustice\b"],
    "EACOP":             [r"\bEACOP\b",r"\bCrude Oil Pipeline\b"],
    "Umeme/ERA":         [r"\bERA\b",r"\bElectricity Regulatory Authority\b"],
}

SECTOR_PATTERNS = {
    "Roads/Transport":  [r"\broad\b",r"\bhighway\b",r"\bbridge\b",r"\bbypass\b",r"\bexpressway\b",r"\bUNRA\b",r"\btraffic\b"],
    "Energy":           [r"\benergy\b",r"\bpower\b",r"\bhydropower\b",r"\belectricit\b",r"\bUmeme\b",r"\bUEGCL\b",r"\bgrid\b",r"\bdam\b",r"\bsolar\b"],
    "Water/Sanitation": [r"\bwater\b",r"\bsewerage\b",r"\bNWSC\b",r"\bsanitation\b",r"\bpipe\b"],
    "Buildings/Housing":[r"\bbuilding\b",r"\bhousing\b",r"\bschool\b",r"\bhospital\b",r"\bconstruct\b"],
    "Oil/Gas":          [r"\boil\b",r"\bpipeline\b",r"\bEACOP\b",r"\bpetroleum\b",r"\brefinery\b"],
    "Land/Urban":       [r"\bland\b",r"\bcompensation\b",r"\bevict\b",r"\bKCCA\b",r"\bplot\b"],
}

def compile_pats(d):
    return {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in d.items()}

def match_first(text, compiled):
    for name, pats in compiled.items():
        if any(p.search(text) for p in pats):
            return name
    return "Other"

def match_all(text, compiled):
    return sorted({k for k, pats in compiled.items() if any(p.search(text) for p in pats)})

def cramers_v(a, b):
    ct = pd.crosstab(a, b)
    chi2 = chi2_contingency(ct, correction=False)[0]
    n = ct.values.sum()
    r, c = ct.shape
    return float(np.sqrt(chi2 / (n * (min(r, c) - 1)))) if min(r, c) > 1 else 0.0

def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIG / name, dpi=DPI, bbox_inches="tight")
    plt.close(fig)

# ── Louvain community detection (pure networkx fallback) ────────────────────
def louvain_communities(G):
    try:
        from community import best_partition
        part = best_partition(G, weight="weight")
        coms = defaultdict(list)
        for node, cid in part.items():
            coms[cid].append(node)
        return list(coms.values())
    except ImportError:
        from networkx.algorithms.community import greedy_modularity_communities
        return [list(c) for c in greedy_modularity_communities(G, weight="weight")]

def main():
    print("Loading data …")
    sent = pd.read_csv(BASE / "sentences_classified.csv")
    arts = pd.read_csv(BASE / "articles_relevant.csv")
    sent["year"] = pd.to_datetime(sent["publication_date"], errors="coerce").dt.year
    sent["month_dt"] = pd.to_datetime(sent["publication_date"], errors="coerce").dt.to_period("M")
    arts["year"] = pd.to_datetime(arts["publication_date"], errors="coerce").dt.year
    sent = sent[(sent["year"] >= 2016) & (sent["year"] <= 2025)].copy()
    arts = arts[(arts["year"] >= 2016) & (arts["year"] <= 2025)].copy()

    cpats = compile_pats(ACTOR_PATTERNS)
    spats = compile_pats(SECTOR_PATTERNS)

    # ── Sector tagging of articles ───────────────────────────────────────────
    print("Sector tagging …")
    full_text = (arts["title"].fillna("") + ". " + arts["text"].fillna(""))
    arts["sector"] = full_text.apply(lambda t: match_first(t, spats))

    # ── Actor hits per article ───────────────────────────────────────────────
    arts["actor_hits"] = full_text.apply(lambda t: match_all(t, cpats))
    arts["actor_type_hits"] = arts["actor_hits"].apply(
        lambda lst: sorted({ACTOR_TYPE.get(a, "Other") for a in lst})
    )

    # Merge driver (pred_final) mode per article
    mode_driver = (
        sent.groupby("url")["pred_final"]
        .agg(lambda s: s.value_counts().idxmax() if len(s) else np.nan)
        .reset_index()
        .rename(columns={"pred_final": "main_driver"})
    )
    arts = arts.merge(mode_driver, on="url", how="left")
    arts["main_driver_nice"] = arts["main_driver"].map(NICE_NAMES).fillna(arts["main_driver"])

    # First location → region
    loc_pats = {loc: re.compile(rf"\b{re.escape(loc)}\b", re.IGNORECASE) for loc in LOCATION_REGION}
    def first_region(text):
        for loc, pat in loc_pats.items():
            if pat.search(text):
                return LOCATION_REGION[loc]
        return "Unknown"
    arts["region"] = full_text.apply(first_region)

    arts.to_csv(EXTOUT / "articles_extended.csv", index=False)

    # ── A. Dispute co-occurrence within articles ─────────────────────────────
    print("Dispute co-occurrence …")
    cats = list(NICE_NAMES.keys())
    cooc = pd.DataFrame(0, index=cats, columns=cats, dtype=int)
    art_cats = sent.groupby("url")["pred_final"].apply(lambda s: list(s.unique())).reset_index()
    for _, row in art_cats.iterrows():
        for a, b in itertools.combinations(sorted(row["pred_final"]), 2):
            if a in cooc.index and b in cooc.columns:
                cooc.loc[a, b] += 1
                cooc.loc[b, a] += 1
        for a in row["pred_final"]:
            if a in cooc.index:
                cooc.loc[a, a] += 1
    cooc.index   = [NICE_NAMES.get(c, c) for c in cooc.index]
    cooc.columns = [NICE_NAMES.get(c, c) for c in cooc.columns]
    cooc.to_csv(EXTOUT / "dispute_cooccurrence_matrix.csv")

    fig, ax = plt.subplots(figsize=(11, 8))
    mask = np.zeros_like(cooc, dtype=bool)
    np.fill_diagonal(mask, False)
    sns.heatmap(cooc, annot=True, fmt="d", cmap="Blues", ax=ax,
                linewidths=0.5, linecolor="white")
    ax.set_title("Dispute Category Co-occurrence Within Articles")
    plt.xticks(rotation=35, ha="right")
    save(fig, "ext_figure_01_dispute_cooccurrence_heatmap.png")

    # ── B. Cramér's V correlation matrices ──────────────────────────────────
    print("Cramér's V matrices …")
    arts_clean = arts.dropna(subset=["main_driver", "sector", "region"]).copy()
    arts_clean = arts_clean[arts_clean["region"] != "Unknown"]

    def cramers_matrix(df, col_a, col_b, labels_a, labels_b):
        rows = []
        for a in labels_a:
            row = []
            for b in labels_b:
                sub_a = (df[col_a] == a).astype(int)
                sub_b = (df[col_b] == b).astype(int)
                try:
                    v = cramers_v(df[col_a], df[col_b])
                except Exception:
                    v = 0.0
                row.append(v)
            rows.append(row)
        return pd.DataFrame(rows, index=labels_a, columns=labels_b)

    # Driver × Region
    regions = [r for r in arts_clean["region"].unique() if r != "Unknown"]
    drivers = sorted(arts_clean["main_driver"].dropna().unique())
    dr_matrix = pd.DataFrame(index=[NICE_NAMES.get(d, d) for d in drivers], columns=regions, dtype=float)
    for d in drivers:
        for r in regions:
            sub = arts_clean.copy()
            sub["d_flag"] = (sub["main_driver"] == d).astype(int)
            sub["r_flag"] = (sub["region"] == r).astype(int)
            ct = pd.crosstab(sub["d_flag"], sub["r_flag"])
            try:
                chi2 = chi2_contingency(ct, correction=False)[0]
                n = ct.values.sum()
                v = float(np.sqrt(chi2 / n)) if n > 0 else 0.0
            except Exception:
                v = 0.0
            dr_matrix.loc[NICE_NAMES.get(d, d), r] = round(v, 3)
    dr_matrix.to_csv(EXTOUT / "cramers_driver_region.csv")

    fig, ax = plt.subplots(figsize=(12, 6))
    sns.heatmap(dr_matrix.astype(float), annot=True, fmt=".2f", cmap="YlOrRd", ax=ax,
                linewidths=0.4, linecolor="white", vmin=0, vmax=0.4)
    ax.set_title("Cramér's V: Dispute Driver × Region Association")
    plt.xticks(rotation=35, ha="right")
    save(fig, "ext_figure_02_cramers_driver_region.png")

    # Driver × Sector
    sectors = sorted(arts_clean["sector"].unique())
    ds_matrix = pd.DataFrame(index=[NICE_NAMES.get(d, d) for d in drivers], columns=sectors, dtype=float)
    for d in drivers:
        for s in sectors:
            sub = arts_clean.copy()
            sub["d_flag"] = (sub["main_driver"] == d).astype(int)
            sub["s_flag"] = (sub["sector"] == s).astype(int)
            ct = pd.crosstab(sub["d_flag"], sub["s_flag"])
            try:
                chi2 = chi2_contingency(ct, correction=False)[0]
                n = ct.values.sum()
                v = float(np.sqrt(chi2 / n)) if n > 0 else 0.0
            except Exception:
                v = 0.0
            ds_matrix.loc[NICE_NAMES.get(d, d), s] = round(v, 3)
    ds_matrix.to_csv(EXTOUT / "cramers_driver_sector.csv")

    fig, ax = plt.subplots(figsize=(13, 6))
    sns.heatmap(ds_matrix.astype(float), annot=True, fmt=".2f", cmap="YlOrRd", ax=ax,
                linewidths=0.4, linecolor="white", vmin=0, vmax=0.4)
    ax.set_title("Cramér's V: Dispute Driver × Sector Association")
    plt.xticks(rotation=35, ha="right")
    save(fig, "ext_figure_03_cramers_driver_sector.png")

    # ── C. Actor community detection ─────────────────────────────────────────
    print("Actor community detection …")
    edge_df = pd.read_csv(NPONLY / "actor_network_edges.csv")
    G = nx.Graph()
    for _, r in edge_df.iterrows():
        G.add_edge(r["source"], r["target"], weight=float(r["weight"]))
    communities = louvain_communities(G)
    comm_rows = []
    for cid, nodes in enumerate(communities):
        for n in nodes:
            comm_rows.append({"actor": n, "community": cid,
                              "actor_type": ACTOR_TYPE.get(n, "Other")})
    comm_df = pd.DataFrame(comm_rows)
    comm_df.to_csv(EXTOUT / "actor_communities.csv", index=False)

    # Community network figure
    fig, ax = plt.subplots(figsize=(12, 9))
    cmap_com = plt.cm.get_cmap("tab10", len(communities))
    node_colors = {}
    for cid, nodes in enumerate(communities):
        for n in nodes:
            node_colors[n] = cmap_com(cid)
    pos = nx.spring_layout(G, seed=42, weight="weight")
    maxw = max((G[u][v]["weight"] for u, v in G.edges), default=1)
    widths = [0.5 + 4 * G[u][v]["weight"] / maxw for u, v in G.edges]
    nx.draw_networkx_edges(G, pos, width=widths, alpha=0.35, ax=ax)
    nx.draw_networkx_nodes(G, pos,
                           node_color=[node_colors.get(n, "grey") for n in G.nodes],
                           node_size=900, ax=ax)
    nx.draw_networkx_labels(G, pos, font_size=8, ax=ax)
    patches = [mpatches.Patch(color=cmap_com(i), label=f"Community {i+1}")
               for i in range(len(communities))]
    ax.legend(handles=patches, loc="lower left", frameon=True)
    ax.axis("off")
    ax.set_title("Actor Co-occurrence Network with Community Detection")
    save(fig, "ext_figure_04_actor_communities.png")

    # ── D. Temporal series with election/event annotations ──────────────────
    print("Temporal annotated trends …")
    yearly = (
        sent.pivot_table(index="year", columns="pred_final", values="sentence",
                         aggfunc="count", fill_value=0).sort_index()
    )
    EVENTS = {
        2016: "Election\n(Feb)",
        2020: "COVID-19\nDisruption",
        2021: "Election\n(Jan)",
    }

    fig, ax = plt.subplots(figsize=(13, 7))
    for col in yearly.columns:
        ax.plot(yearly.index, yearly[col], marker="o", linewidth=2,
                label=NICE_NAMES.get(col, col))
    for yr, label in EVENTS.items():
        ax.axvline(yr, color="grey", linestyle="--", alpha=0.6)
        ax.text(yr + 0.1, ax.get_ylim()[1] * 0.93, label, fontsize=8,
                color="grey", va="top")
    ax.set_title("Annual Dispute Driver Trends with Key Events (2016–2025)")
    ax.set_xlabel("Year"); ax.set_ylabel("Classified sentence count")
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=True, fontsize=9)
    save(fig, "ext_figure_05_temporal_annotated.png")

    # Stacked bar version
    fig, ax = plt.subplots(figsize=(13, 7))
    nice_cols = {c: NICE_NAMES.get(c, c) for c in yearly.columns}
    yearly_nice = yearly.rename(columns=nice_cols)
    yearly_nice.plot(kind="bar", stacked=True, ax=ax,
                     colormap="tab10", width=0.75)
    for yr, label in EVENTS.items():
        if yr in yearly.index:
            idx = list(yearly.index).index(yr)
            ax.axvline(idx, color="black", linestyle=":", alpha=0.7)
    ax.set_title("Stacked Dispute Categories by Year with Event Markers")
    ax.set_xlabel("Year"); ax.set_ylabel("Sentence count")
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=True, fontsize=8)
    plt.xticks(rotation=0)
    save(fig, "ext_figure_06_temporal_stacked_events.png")

    # Monthly series with prominence peaks
    monthly = (
        sent.groupby("month_dt")["sentence"].count()
        .reset_index()
        .rename(columns={"sentence": "count"})
        .sort_values("month_dt")
    )
    monthly["month_str"] = monthly["month_dt"].astype(str)
    peaks, _ = find_peaks(monthly["count"].values, height=np.percentile(monthly["count"].values, 75),
                           distance=3)
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(monthly.index, monthly["count"], linewidth=1.5, color="#4e79a7")
    ax.fill_between(monthly.index, monthly["count"], alpha=0.25, color="#4e79a7")
    ax.scatter(peaks, monthly["count"].values[peaks], color="#e15759", zorder=5, s=60,
               label="Peak month")
    tick_idx = np.arange(0, len(monthly), 12)
    ax.set_xticks(tick_idx)
    ax.set_xticklabels([monthly["month_str"].iloc[i][:7] for i in tick_idx], rotation=35, ha="right")
    ax.set_title("Monthly Dispute Sentence Volume (2016–2025) with Peak Detection")
    ax.set_xlabel("Month"); ax.set_ylabel("Sentence count")
    ax.legend()
    save(fig, "ext_figure_07_monthly_peaks.png")

    # ── E. Sector distribution ──────────────────────────────────────────────
    print("Sector analysis …")
    sec_counts = arts_clean["sector"].value_counts()
    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    sec_counts.sort_values().plot(kind="barh", ax=ax, color="#76b7b2")
    ax.set_title("Articles by Infrastructure Sector")
    ax.set_xlabel("Article count")
    save(fig, "ext_figure_08_sector_distribution.png")

    # Sector × driver heatmap (counts)
    sec_driver = pd.crosstab(arts_clean["sector"], arts_clean["main_driver"])
    sec_driver.columns = [NICE_NAMES.get(c, c) for c in sec_driver.columns]
    sec_driver.to_csv(EXTOUT / "sector_driver_crosstab.csv")
    fig, ax = plt.subplots(figsize=(14, 6))
    sns.heatmap(sec_driver, annot=True, fmt="d", cmap="Blues", ax=ax,
                linewidths=0.4, linecolor="white")
    ax.set_title("Dispute Driver × Infrastructure Sector (Article Count)")
    plt.xticks(rotation=35, ha="right")
    save(fig, "ext_figure_09_sector_driver_heatmap.png")

    # ── F. Actor-type breakdown ──────────────────────────────────────────────
    print("Actor-type analysis …")
    actor_type_counts = Counter()
    for lst in arts["actor_type_hits"]:
        actor_type_counts.update(lst)
    atdf = pd.DataFrame(actor_type_counts.items(), columns=["actor_type", "article_count"])\
             .sort_values("article_count", ascending=False)
    atdf.to_csv(EXTOUT / "actor_type_counts.csv", index=False)

    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    atdf_plot = atdf[atdf["actor_type"] != "Other"].sort_values("article_count")
    ax.barh(atdf_plot["actor_type"], atdf_plot["article_count"], color="#af7aa1")
    ax.set_title("Actor-Type Frequency in Observer Risk Articles")
    ax.set_xlabel("Article count")
    save(fig, "ext_figure_10_actor_type_distribution.png")

    # Actor-type × driver heatmap
    rows_at = []
    for _, r in arts_clean.iterrows():
        for at in r["actor_type_hits"]:
            rows_at.append({"actor_type": at, "main_driver": r["main_driver"]})
    at_df = pd.DataFrame(rows_at)
    if not at_df.empty:
        at_driver = pd.crosstab(at_df["actor_type"], at_df["main_driver"])
        at_driver.columns = [NICE_NAMES.get(c, c) for c in at_driver.columns]
        at_driver.to_csv(EXTOUT / "actor_type_driver_crosstab.csv")
        fig, ax = plt.subplots(figsize=(14, 7))
        sns.heatmap(at_driver, annot=True, fmt="d", cmap="YlGnBu", ax=ax,
                    linewidths=0.4, linecolor="white")
        ax.set_title("Actor-Type × Dispute Driver (Article Count)")
        plt.xticks(rotation=35, ha="right")
        save(fig, "ext_figure_11_actor_type_driver_heatmap.png")

    # ── G. Tripartite summary table: actor × driver × region ────────────────
    print("Tripartite table …")
    rows_tri = []
    for _, r in arts_clean.iterrows():
        for actor in r["actor_hits"]:
            rows_tri.append({
                "actor":  actor,
                "driver": r["main_driver"],
                "region": r["region"],
                "sector": r["sector"],
            })
    tri_df = pd.DataFrame(rows_tri)
    tri_df.to_csv(EXTOUT / "tripartite_actor_driver_region.csv", index=False)

    # Pivot: top 10 actors × driver
    top_actors = [a for a, _ in Counter(tri_df["actor"]).most_common(10)]
    tri_pivot = pd.crosstab(tri_df["actor"], tri_df["driver"])
    tri_pivot.columns = [NICE_NAMES.get(c, c) for c in tri_pivot.columns]
    tri_pivot_top = tri_pivot.loc[tri_pivot.index.isin(top_actors)]
    tri_pivot_top.to_csv(EXTOUT / "top_actor_by_driver.csv")
    fig, ax = plt.subplots(figsize=(14, 7))
    sns.heatmap(tri_pivot_top, annot=True, fmt="d", cmap="Oranges", ax=ax,
                linewidths=0.4, linecolor="white")
    ax.set_title("Top Actors × Dispute Driver Association (Article Count)")
    plt.xticks(rotation=35, ha="right")
    save(fig, "ext_figure_12_actor_driver_heatmap.png")

    # ── H. Region × year trend ─────────────────────────────────────────────
    ryt = pd.read_csv(NPONLY / "region_year_heatmap_table.csv", index_col=0)
    print("Region year trend …")

    fig, ax = plt.subplots(figsize=(12, 5))
    for region in ryt.index:
        ax.plot(ryt.columns.astype(int), ryt.loc[region], marker="o", linewidth=2, label=region)
    ax.set_title("Regional Dispute Reporting Trend (2016–2025)")
    ax.set_xlabel("Year"); ax.set_ylabel("Article count")
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=True, fontsize=8)
    save(fig, "ext_figure_13_region_trends.png")

    # ── I. Community membership table summary ──────────────────────────────
    comm_summary = {}
    for cid, nodes in enumerate(communities):
        types = [ACTOR_TYPE.get(n, "Other") for n in nodes]
        comm_summary[f"Community_{cid+1}"] = {
            "members": nodes,
            "actor_types": sorted(set(types)),
            "size": len(nodes),
        }
    with open(EXTOUT / "community_summary.json", "w") as f:
        json.dump(comm_summary, f, indent=2)

    # ── Summary JSON ────────────────────────────────────────────────────────
    summary = {
        "articles_with_sector": int(len(arts_clean)),
        "top_sector": sec_counts.idxmax() if not sec_counts.empty else "n/a",
        "num_actor_communities": len(communities),
        "community_sizes": [len(c) for c in communities],
        "dispute_cooccurrence_top_pair": cooc.stack().drop(
            index=[(c, c) for c in cooc.index], errors="ignore"
        ).idxmax() if cooc.stack().shape[0] > 0 else None,
    }
    with open(EXTOUT / "extended_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(json.dumps(summary, indent=2, default=str))
    print(f"\nAll extended outputs saved to: {EXTOUT}")
    print(f"Figures saved to:              {FIG}")


if __name__ == "__main__":
    main()
