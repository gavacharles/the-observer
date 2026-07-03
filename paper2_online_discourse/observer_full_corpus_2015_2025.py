#!/usr/bin/env python3
"""
observer_full_corpus_2015_2025.py
===================================
Full-corpus scraper for The Observer (Uganda) – 2015 to 2025.

Strategy
--------
1. Collect every URL from all Observer post-sitemaps that falls in
   the target year range (2015–2025).
2. Apply a broad URL-slug keyword pre-filter to retain only articles
   that are plausibly about construction, infrastructure, contracts,
   or related dispute categories.  This reduces ~32 K candidate URLs
   to a manageable downloadable set while preserving high recall.
3. Download every pre-filtered article (no per-year cap).
4. Apply the 8-stage text-mining pipeline:
      A  Sentence segmentation
      B  Relevance filter  (vocabulary intersection)
      C  Keyword weak-labelling
      D  TF-IDF + LinearSVC content classification
      E  Semantic similarity classification (sentence-transformers
         or TF-IDF centroid fallback)
      F  Prediction fusion
      G  Monthly aggregation
      H  CSV / JSON export
5. Write a full semantic-classification-ready dataset plus run summary.

Outputs (all in --output-dir)
-------------------------------
  all_sitemap_urls.csv          – every sitemap URL with its date (before filter)
  prefiltered_urls.csv          – URLs that passed the slug keyword filter
  articles_raw.csv              – successfully downloaded + parsed articles
  articles_relevant.csv         – articles that contain ≥1 relevant sentence
  sentences_classified.csv      – sentence-level classified corpus
  monthly_dispute_signals.csv   – monthly aggregated signal counts
  semantic_dataset.csv          – clean sentence + label table for ML training
  run_summary.json              – full run statistics

Usage
-----
  python observer_full_corpus_2015_2025.py \\
      --config scrape_config_paper2.json \\
      --start-year 2015 \\
      --end-year   2025 \\
      --output-dir outputs_observer_full_corpus \\
      --sleep      0.8 \\
      --seed       42
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import random
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC
from urllib.parse import urljoin

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ── URL-slug pre-filter keywords ──────────────────────────────────────────────
# Broad set covering all 7 dispute categories + general infrastructure terms.
# Match is OR-based; any hit retains the URL.
URL_FILTER_KEYWORDS: List[str] = [
    # Physical infrastructure
    "road", "highway", "bridge", "dam", "reservoir", "pipeline",
    "hospital", "school", "clinic", "housing", "water", "sanitation",
    "electricity", "power", "energy", "railway", "rail", "airport",
    "stadium", "market", "prison", "barracks", "infrastructure",
    # Project / construction terms
    "construct", "built", "building", "project", "contractor", "contract",
    "tender", "procurement", "bid", "award", "works", "renovation",
    "rehabilitat", "upgrading", "expansion",
    # Dispute / oversight terms
    "corrupt", "scandal", "fraud", "audit", "parliament", "committee",
    "investigate", "probe", "delay", "overrun", "abandon", "stalled",
    "dispute", "payment", "arrears", "compensation", "evict", "land",
    "encroach", "defect", "quality", "substandard", "shoddy",
    "ghost", "inflat", "overpriced", "mismanage",
    # Ugandan institutions & agencies
    "unra", "kcca", "nwsc", "umeme", "uegcl", "ppda", "oag",
    "ministry of works", "ministry of health", "ministry of education",
]


# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────

def to_iso_date(value: str) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    for fmt in ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"]:
        try:
            dt = datetime.strptime(value[:len(fmt)], fmt)
            return dt.date().isoformat()
        except (ValueError, TypeError):
            continue
    if re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    return None


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def sent_split(text: str) -> List[str]:
    text = normalize_text(text)
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\u201c])", text)
    return [p.strip() for p in parts if len(p.strip()) > 25]


def fetch_url(url: str, timeout: int = 25, retries: int = 3) -> Optional[str]:
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, timeout=timeout, headers=HEADERS)
            if r.status_code == 200:
                return r.text
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"\n  Rate-limited – sleeping {wait}s…", flush=True)
                time.sleep(wait)
        except requests.RequestException as exc:
            if attempt < retries:
                time.sleep(3)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Stage A – Sitemap crawl
# ──────────────────────────────────────────────────────────────────────────────

def collect_sitemap_urls(start_year: int, end_year: int, sleep: float = 1.0) -> List[Tuple[str, str]]:
    """Return all (url, ISO-date) pairs from Observer post-sitemaps in year range."""
    print("Fetching Observer sitemap index…", flush=True)
    idx_xml = fetch_url("https://observer.ug/sitemap_index.xml")
    if not idx_xml:
        raise RuntimeError("Cannot fetch Observer sitemap index – check connectivity.")

    soup = BeautifulSoup(idx_xml, "xml")
    post_maps = [
        sm.find("loc").text.strip()
        for sm in soup.find_all("sitemap")
        if sm.find("loc") and "post-sitemap" in sm.find("loc").text
    ]
    print(f"  {len(post_maps)} post-sitemaps found.", flush=True)

    all_pairs: List[Tuple[str, str]] = []
    for i, sm_url in enumerate(post_maps, 1):
        print(f"\r  Reading sitemap {i}/{len(post_maps)} …", end="", flush=True)
        xml = fetch_url(sm_url)
        if not xml:
            continue
        s = BeautifulSoup(xml, "xml")
        for url_tag in s.find_all("url"):
            loc_tag = url_tag.find("loc")
            lm_tag  = url_tag.find("lastmod")
            if not loc_tag or not lm_tag:
                continue
            date = to_iso_date(lm_tag.text.strip()) or ""
            if not date:
                continue
            yr = int(date[:4])
            if start_year <= yr <= end_year:
                all_pairs.append((loc_tag.text.strip(), date))
        time.sleep(sleep)
    print(f"\n  Total URLs in {start_year}–{end_year}: {len(all_pairs):,}", flush=True)
    return all_pairs


# ──────────────────────────────────────────────────────────────────────────────
# Stage B – URL pre-filter
# ──────────────────────────────────────────────────────────────────────────────

def url_keyword_filter(pairs: List[Tuple[str, str]], keywords: List[str]) -> List[Tuple[str, str]]:
    """Retain only URL pairs whose slug contains at least one keyword."""
    kw_lower = [k.lower() for k in keywords]
    kept = [(url, date) for url, date in pairs
            if any(kw in url.lower() for kw in kw_lower)]
    return kept


# ──────────────────────────────────────────────────────────────────────────────
# Stage C – Article download + parse
# ──────────────────────────────────────────────────────────────────────────────

def parse_article(html: str, url: str, fallback_date: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")

    # Title
    h1 = soup.find("h1")
    title = normalize_text(h1.get_text(" ", strip=True)) if h1 else ""

    # Publication date (multiple strategies)
    date_txt = ""
    for meta_attr, meta_val in [("property", "article:published_time"),
                                  ("name", "publish-date"),
                                  ("name", "date")]:
        m = soup.find("meta", attrs={meta_attr: meta_val})
        if m and m.get("content"):
            date_txt = m["content"]
            break
    if not date_txt:
        for sel in ["time[datetime]", "time", "span.published", "span.date",
                    "p.date", ".article-date", ".post-date"]:
            el = soup.select_one(sel)
            if el:
                date_txt = el.get("datetime") or normalize_text(el.get_text(" ", strip=True))
                break

    # Body text
    body = soup.select_one(
        "div[itemprop='articleBody'], div.articleBody, div.blog-content, "
        "div.article-body, article .content, article"
    )
    if body:
        paras = [normalize_text(p.get_text(" ", strip=True)) for p in body.find_all("p")]
    else:
        paras = [normalize_text(p.get_text(" ", strip=True)) for p in soup.find_all("p")]
    text = " ".join(p for p in paras if p)

    return {
        "url": url,
        "title": title,
        "publication_date": to_iso_date(date_txt) or fallback_date,
        "text": normalize_text(text),
        "word_count": len(text.split()),
    }


def download_articles(pairs: List[Tuple[str, str]], sleep: float,
                      out_dir: str, checkpoint_file: str) -> pd.DataFrame:
    """Download articles; supports resume via checkpoint CSV."""
    done_urls: set = set()
    rows: List[Dict] = []

    if os.path.exists(checkpoint_file):
        df_cp = pd.read_csv(checkpoint_file)
        rows = df_cp.to_dict("records")
        done_urls = set(df_cp["url"].dropna())
        print(f"  Resuming from checkpoint – {len(done_urls)} already downloaded.", flush=True)

    remaining = [(u, d) for u, d in pairs if u not in done_urls]
    total = len(pairs)
    done = len(done_urls)

    for i, (url, feed_date) in enumerate(remaining, 1):
        pct = 100 * (done + i) / total
        if (done + i) % 50 == 0 or i == 1:
            print(f"\r  [{done+i}/{total}  {pct:.1f}%] …", end="", flush=True)
        html = fetch_url(url)
        if not html:
            continue
        parsed = parse_article(html, url, feed_date)
        if not parsed.get("text") or parsed["word_count"] < 80:
            continue
        rows.append({"source": "The Observer", **parsed})
        time.sleep(sleep)

        # Save checkpoint every 100 articles
        if len(rows) % 100 == 0:
            pd.DataFrame(rows).to_csv(checkpoint_file, index=False)

    print(flush=True)
    df = pd.DataFrame(rows).drop_duplicates(subset=["url"]).reset_index(drop=True)
    df.to_csv(checkpoint_file, index=False)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# Stages D–H – Text-mining pipeline (identical logic to main pipeline)
# ──────────────────────────────────────────────────────────────────────────────

def relevance_mask(series: pd.Series, terms: List[str]) -> pd.Series:
    ordered = sorted({t.lower().strip() for t in terms if t and t.strip()}, key=len, reverse=True)
    pattern = "|".join([rf"(?<!\w){re.escape(t)}(?!\w)" for t in ordered])
    return series.str.lower().str.contains(pattern, regex=True, na=False)


def construction_relevance_mask(df_sent: pd.DataFrame, cfg: Dict) -> pd.Series:
    text_series = (df_sent["title"].fillna("") + " " + df_sent["sentence"].fillna("")).str.lower()
    infra = relevance_mask(text_series, cfg.get("infrastructure_terms", []))
    risk  = relevance_mask(text_series, cfg.get("risk_terms", []))
    proc  = relevance_mask(text_series, cfg.get("construction_process_terms", []))
    return infra | risk | proc


def weak_label(sentence: str, category_keywords: Dict[str, List[str]]) -> Optional[str]:
    s = sentence.lower()
    scores = {cat: sum(1 for k in kws if k in s) for cat, kws in category_keywords.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def build_sentence_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        for sent in sent_split(r["text"]):
            rows.append({
                "source": "The Observer",
                "url": r["url"],
                "publication_date": r["publication_date"],
                "title": r["title"],
                "sentence": sent,
            })
    return pd.DataFrame(rows)


def train_content_classifier(df_sent: pd.DataFrame) -> Pipeline:
    pipe = Pipeline([
        ("tfidf", TfidfVectorizer(
            ngram_range=(1, 2), min_df=2, max_features=40000, stop_words="english",
            sublinear_tf=True,
        )),
        ("clf", LinearSVC(class_weight="balanced", random_state=42, max_iter=5000)),
    ])
    X, y = df_sent["sentence"], df_sent["label_content"]
    label_counts = y.value_counts(dropna=True)
    can_stratify = not label_counts.empty and int(label_counts.min()) >= 2
    if len(df_sent) >= max(20, y.nunique() * 4) and can_stratify:
        X_tr, _, y_tr, _ = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
        pipe.fit(X_tr, y_tr)
    else:
        pipe.fit(X, y)
    return pipe


def semantic_predict(
    sentences: List[str], prototype_map: Dict[str, List[str]]
) -> Tuple[List[str], List[float]]:
    labels = list(prototype_map.keys())
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        proto_vecs = {
            lbl: np.mean(model.encode(prototype_map[lbl], normalize_embeddings=True), axis=0)
            for lbl in labels
        }
        sent_vecs = model.encode(sentences, normalize_embeddings=True, batch_size=64,
                                  show_progress_bar=True)
        pred_labels, confs = [], []
        for vec in sent_vecs:
            sims = {lbl: float(np.dot(vec, proto_vecs[lbl])) for lbl in labels}
            best = max(sims, key=sims.get)
            pred_labels.append(best)
            confs.append(sims[best])
        return pred_labels, confs
    except Exception as e:
        print(f"  sentence-transformers unavailable ({e}); using TF-IDF centroid fallback.", flush=True)
        corpus = sentences[:]
        for lbl in labels:
            corpus.extend(prototype_map[lbl])
        tfidf = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", min_df=1, sublinear_tf=True)
        mat = tfidf.fit_transform(corpus)
        n = len(sentences)
        sent_mat = mat[:n]
        proto_mats, idx = {}, n
        for lbl in labels:
            m = len(prototype_map[lbl])
            proto_mats[lbl] = mat[idx: idx + m].mean(axis=0)
            idx += m
        pred_labels, confs = [], []
        for i in range(n):
            sims = {lbl: float(cosine_similarity(sent_mat[i], proto_mats[lbl])[0, 0]) for lbl in labels}
            best = max(sims, key=sims.get)
            pred_labels.append(best)
            confs.append(sims[best])
        return pred_labels, confs


def fuse_predictions(content_label, semantic_label, content_conf, semantic_conf):
    if content_label == semantic_label:
        return content_label, min(0.99, 0.5 + 0.25 * content_conf + 0.25 * semantic_conf), "agree"
    if semantic_conf - content_conf > 0.20:
        return semantic_label, max(0.50, semantic_conf), "semantic_override"
    return content_label, max(0.50, content_conf), "content_preferred"


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Full Observer corpus scraper and classifier 2015–2025"
    )
    parser.add_argument("--config",     default="scrape_config_paper2.json",
                        help="Path to scrape config JSON")
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year",   type=int, default=2025)
    parser.add_argument("--output-dir", default="outputs_observer_full_corpus")
    parser.add_argument("--sleep",      type=float, default=0.8,
                        help="Seconds to sleep between HTTP requests (default 0.8)")
    parser.add_argument("--seed",       type=int, default=42)
    parser.add_argument("--no-url-filter", action="store_true",
                        help="Disable URL-slug pre-filter (downloads all articles – very slow)")
    parser.add_argument("--min-words",  type=int, default=80,
                        help="Minimum word count to retain an article (default 80)")
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    os.makedirs(args.output_dir, exist_ok=True)

    cfg = json.load(open(args.config, encoding="utf-8"))

    # ── Stage 1: Collect sitemap URLs ─────────────────────────────────────────
    print("\n" + "="*60)
    print(f"STAGE 1 – Sitemap crawl  ({args.start_year}–{args.end_year})")
    print("="*60)
    all_pairs = collect_sitemap_urls(args.start_year, args.end_year, sleep=args.sleep)

    df_all_urls = pd.DataFrame(all_pairs, columns=["url", "date"])
    df_all_urls.to_csv(os.path.join(args.output_dir, "all_sitemap_urls.csv"), index=False)
    print(f"  Saved all_sitemap_urls.csv  ({len(df_all_urls):,} URLs)")

    # Year distribution
    df_all_urls["year"] = df_all_urls["date"].str[:4].astype(int, errors="ignore")
    yd = df_all_urls["year"].value_counts().sort_index()
    print("\n  Year distribution (all sitemap URLs):")
    for yr, cnt in yd.items():
        print(f"    {yr}: {cnt:,}")

    # ── Stage 2: URL pre-filter ───────────────────────────────────────────────
    print("\n" + "="*60)
    print("STAGE 2 – URL-slug keyword pre-filter")
    print("="*60)
    if args.no_url_filter:
        filtered_pairs = all_pairs
        print(f"  URL filter DISABLED – all {len(filtered_pairs):,} URLs will be downloaded.")
    else:
        filtered_pairs = url_keyword_filter(all_pairs, URL_FILTER_KEYWORDS)
        print(f"  Pre-filter keywords: {len(URL_FILTER_KEYWORDS)} terms")
        print(f"  URLs before filter : {len(all_pairs):,}")
        print(f"  URLs after filter  : {len(filtered_pairs):,}  "
              f"({100*len(filtered_pairs)/max(1,len(all_pairs)):.1f}% of total)")

    df_filt = pd.DataFrame(filtered_pairs, columns=["url", "date"])
    df_filt.to_csv(os.path.join(args.output_dir, "prefiltered_urls.csv"), index=False)

    # Year distribution after filter
    df_filt["year"] = df_filt["date"].str[:4].astype(int, errors="ignore")
    yd2 = df_filt["year"].value_counts().sort_index()
    print("\n  Year distribution (after URL filter):")
    for yr, cnt in yd2.items():
        print(f"    {yr}: {cnt:,}")

    random.shuffle(filtered_pairs)

    # ── Stage 3: Download articles ────────────────────────────────────────────
    print("\n" + "="*60)
    print(f"STAGE 3 – Downloading {len(filtered_pairs):,} articles")
    print(f"  Sleep between requests: {args.sleep}s")
    est_min = len(filtered_pairs) * args.sleep / 60
    print(f"  Estimated time (no failures): ~{est_min:.0f} minutes")
    print("="*60)

    checkpoint_file = os.path.join(args.output_dir, "articles_raw.csv")
    df_articles = download_articles(filtered_pairs, args.sleep, args.output_dir, checkpoint_file)
    print(f"\n  Articles downloaded & parsed: {len(df_articles):,}")

    if df_articles.empty:
        print("No articles parsed. Exiting.")
        return

    df_articles["year"] = pd.to_datetime(df_articles["publication_date"], errors="coerce").dt.year
    print("\n  Articles per year:")
    print(df_articles["year"].value_counts().sort_index().to_string())

    # ── Stage 4: Sentence segmentation ───────────────────────────────────────
    print("\n" + "="*60)
    print("STAGE 4 – Sentence segmentation")
    print("="*60)
    sent_df = build_sentence_table(df_articles)
    print(f"  Total sentences: {len(sent_df):,}")

    # ── Stage 5: Relevance filter ─────────────────────────────────────────────
    print("\n" + "="*60)
    print("STAGE 5 – Relevance filter")
    print("="*60)
    sent_df["is_relevant"] = construction_relevance_mask(sent_df, cfg)
    sent_df_rel = sent_df[sent_df["is_relevant"]].copy()
    print(f"  Relevant sentences : {len(sent_df_rel):,}  "
          f"({100*len(sent_df_rel)/max(1,len(sent_df)):.1f}% of all sentences)")

    if sent_df_rel.empty:
        print("  No relevant sentences found. Check vocabulary in config.")
        return

    relevant_articles = df_articles[df_articles["url"].isin(sent_df_rel["url"].unique())].copy()
    relevant_articles.to_csv(os.path.join(args.output_dir, "articles_relevant.csv"), index=False)
    print(f"  Relevant articles  : {len(relevant_articles):,}")

    # ── Stage 6: Keyword weak labelling ──────────────────────────────────────
    print("\n" + "="*60)
    print("STAGE 6 – Keyword weak labelling + Content classification")
    print("="*60)
    sent_df_rel["label_content"] = sent_df_rel["sentence"].apply(
        lambda s: weak_label(s, cfg["category_keywords"])
    )
    train_df = sent_df_rel.dropna(subset=["label_content"]).copy()
    if train_df.empty or train_df["label_content"].nunique() < 2:
        bootstrap = [
            {"sentence": ex, "label_content": lbl}
            for lbl, exs in cfg["semantic_prototypes"].items()
            for ex in exs
        ]
        train_df = pd.DataFrame(bootstrap)
        print("  Using prototype sentences to bootstrap classifier.")
    else:
        lc = train_df["label_content"].value_counts()
        print("  Weak-label distribution:")
        for cat, cnt in lc.items():
            print(f"    {cat}: {cnt:,}")

    content_model = train_content_classifier(train_df[["sentence", "label_content"]])
    sent_df_rel = sent_df_rel.copy()
    sent_df_rel["pred_content"] = content_model.predict(sent_df_rel["sentence"])
    try:
        d = content_model.decision_function(sent_df_rel["sentence"])
        conf = np.abs(d) if d.ndim == 1 else np.max(np.abs(d), axis=1)
        conf = (conf - conf.min()) / (conf.max() - conf.min() + 1e-9)
        sent_df_rel["conf_content"] = conf
    except Exception:
        sent_df_rel["conf_content"] = 0.60

    # ── Stage 7: Semantic classification ─────────────────────────────────────
    print("\n" + "="*60)
    print("STAGE 7 – Semantic classification")
    print("="*60)
    sem_labels, sem_confs = semantic_predict(
        sent_df_rel["sentence"].tolist(), cfg["semantic_prototypes"]
    )
    sent_df_rel["pred_semantic"] = sem_labels
    sent_df_rel["conf_semantic"] = sem_confs

    # ── Stage 8: Prediction fusion ────────────────────────────────────────────
    print("\n" + "="*60)
    print("STAGE 8 – Prediction fusion")
    print("="*60)
    fused = sent_df_rel.apply(
        lambda r: fuse_predictions(
            r["pred_content"], r["pred_semantic"],
            float(r["conf_content"]), float(r["conf_semantic"])
        ),
        axis=1,
    )
    sent_df_rel[["pred_final", "conf_final", "fusion_rule"]] = pd.DataFrame(
        fused.tolist(), index=sent_df_rel.index
    )
    print("  Fusion rule distribution:")
    print(sent_df_rel["fusion_rule"].value_counts().to_string())

    # ── Stage 9: Monthly aggregation ─────────────────────────────────────────
    sent_df_rel["month"] = sent_df_rel["publication_date"].apply(
        lambda d: d[:7] if d and len(d) >= 7 else None
    )
    monthly_agg = (
        sent_df_rel
        .groupby(["month", "source", "pred_final"], dropna=True)
        .size()
        .reset_index(name="count")
        .sort_values(["month", "count"], ascending=[True, False])
    )

    # ── Stage 10: Export ──────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("STAGE 10 – Exporting outputs")
    print("="*60)
    sent_df_rel.to_csv(os.path.join(args.output_dir, "sentences_classified.csv"), index=False)
    monthly_agg.to_csv(os.path.join(args.output_dir, "monthly_dispute_signals.csv"), index=False)

    # Clean semantic training dataset (high-confidence sentences with labels)
    semantic_ds = sent_df_rel[["sentence", "pred_final", "conf_final", "publication_date",
                                "url", "title", "source"]].copy()
    semantic_ds = semantic_ds.rename(columns={"pred_final": "label", "conf_final": "confidence"})
    semantic_ds = semantic_ds.sort_values(["label", "confidence"], ascending=[True, False])
    semantic_ds.to_csv(os.path.join(args.output_dir, "semantic_dataset.csv"), index=False)

    # Category distribution
    cat_dist = sent_df_rel["pred_final"].value_counts()
    print("  Final category distribution:")
    for cat, cnt in cat_dist.items():
        print(f"    {cat}: {cnt:,}")

    # Year distribution of relevant sentences
    yr_dist_sent = (
        pd.to_datetime(sent_df_rel["publication_date"], errors="coerce")
        .dt.year.value_counts().sort_index()
    )
    print("\n  Relevant sentences per year:")
    print(yr_dist_sent.to_string())

    # Run summary
    summary = {
        "source": "The Observer",
        "year_range": f"{args.start_year}–{args.end_year}",
        "run_timestamp": datetime.now().isoformat(),
        "total_sitemap_urls": int(len(all_pairs)),
        "prefiltered_urls": int(len(filtered_pairs)),
        "url_filter_used": not args.no_url_filter,
        "articles_downloaded": int(len(df_articles)),
        "articles_relevant": int(len(relevant_articles)),
        "total_sentences": int(len(sent_df)),
        "relevant_sentences": int(len(sent_df_rel)),
        "sentence_relevance_rate": round(len(sent_df_rel) / max(1, len(sent_df)), 3),
        "final_categories": sorted(sent_df_rel["pred_final"].dropna().unique().tolist()),
        "category_counts": {k: int(v) for k, v in cat_dist.items()},
        "articles_per_year": df_articles["year"].value_counts().sort_index().to_dict(),
        "relevant_articles_per_year": (
            relevant_articles
            .assign(year=pd.to_datetime(relevant_articles["publication_date"], errors="coerce").dt.year)
            ["year"].value_counts().sort_index().to_dict()
        ),
        "relevant_sentences_per_year": {
            int(k): int(v) for k, v in yr_dist_sent.items()
        },
        "outputs": {
            "all_sitemap_urls": "all_sitemap_urls.csv",
            "prefiltered_urls": "prefiltered_urls.csv",
            "articles_raw": "articles_raw.csv",
            "articles_relevant": "articles_relevant.csv",
            "sentences_classified": "sentences_classified.csv",
            "monthly_dispute_signals": "monthly_dispute_signals.csv",
            "semantic_dataset": "semantic_dataset.csv",
        },
    }
    with open(os.path.join(args.output_dir, "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print("\n" + "="*60)
    print("RUN COMPLETE")
    print("="*60)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
