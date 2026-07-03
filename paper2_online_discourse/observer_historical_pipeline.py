#!/usr/bin/env python3
"""
Observer Historical Pipeline – 2016 to 2026
Stratified across all years so every year gets equal representation.

Usage:
  python observer_historical_pipeline.py \
    --config scrape_config_paper2.json \
    --start-year 2016 \
    --end-year   2026 \
    --per-year   150 \
    --output-dir outputs_observer_full
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
from urllib.parse import urljoin, urlparse

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ──────────────────────────────────────────────
# Utilities (shared with main pipeline)
# ──────────────────────────────────────────────

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
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    return [p.strip() for p in parts if len(p.strip()) > 20]


def fetch_url(url: str, timeout: int = 20, retries: int = 2) -> Optional[str]:
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, timeout=timeout, headers=HEADERS)
            if r.status_code == 200:
                return r.text
            if r.status_code == 429:
                time.sleep(5)
        except requests.RequestException:
            pass
        if attempt < retries:
            time.sleep(2)
    return None


# ──────────────────────────────────────────────
# Sitemap crawling
# ──────────────────────────────────────────────

def get_all_observer_urls(start_year: int, end_year: int, sleep: float = 1.0) -> List[Tuple[str, str]]:
    """Return all (url, date) pairs from Observer sitemaps within the year range."""
    idx_xml = fetch_url("https://observer.ug/sitemap_index.xml")
    if not idx_xml:
        raise RuntimeError("Cannot fetch Observer sitemap index")
    soup = BeautifulSoup(idx_xml, "xml")
    post_maps = [
        sm.find("loc").text.strip()
        for sm in soup.find_all("sitemap")
        if sm.find("loc") and "post-sitemap" in sm.find("loc").text
    ]
    print(f"Found {len(post_maps)} post sitemaps")

    all_pairs: List[Tuple[str, str]] = []
    for i, sm_url in enumerate(post_maps, 1):
        print(f"\rFetching sitemap {i}/{len(post_maps)}…", end="", flush=True)
        xml = fetch_url(sm_url)
        if not xml:
            continue
        s = BeautifulSoup(xml, "xml")
        for url_tag in s.find_all("url"):
            loc = (url_tag.find("loc").text.strip() if url_tag.find("loc") else "")
            lm  = (url_tag.find("lastmod").text.strip() if url_tag.find("lastmod") else "")
            date = to_iso_date(lm) or ""
            if not loc or not date:
                continue
            year = int(date[:4])
            if start_year <= year <= end_year:
                all_pairs.append((loc, date))
        time.sleep(sleep)
    print()
    return all_pairs


def stratified_sample(pairs: List[Tuple[str, str]], per_year: int,
                       allowed_url_keywords: List[str]) -> List[Tuple[str, str]]:
    """Sample up to per_year articles per year, optionally filtered by URL keywords."""
    by_year: Dict[int, List[Tuple[str, str]]] = defaultdict(list)
    for url, date in pairs:
        if allowed_url_keywords and not any(k in url.lower() for k in allowed_url_keywords):
            continue
        year = int(date[:4])
        by_year[year].append((url, date))

    sample = []
    for year in sorted(by_year):
        pool = by_year[year]
        random.shuffle(pool)
        picked = pool[:per_year]
        print(f"  Year {year}: {len(pool):>6} candidates → sampling {len(picked)}")
        sample.extend(picked)
    return sample


# ──────────────────────────────────────────────
# Article parsing
# ──────────────────────────────────────────────

def parse_article(html: str, url: str, fallback_date: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")

    # title
    h1 = soup.find("h1")
    title = normalize_text(h1.get_text(" ", strip=True)) if h1 else ""

    # date
    date_txt = ""
    m = soup.find("meta", attrs={"property": "article:published_time"})
    if m and m.get("content"):
        date_txt = m["content"]
    if not date_txt:
        for sel in ["time", "span.published", "span.date", "p.date"]:
            el = soup.select_one(sel)
            if el:
                date_txt = normalize_text(el.get_text(" ", strip=True))
                break

    # body
    body = soup.select_one("div[itemprop='articleBody'], div.articleBody, div.blog-content, article")
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
    }


# ──────────────────────────────────────────────
# Relevance + classification (same logic as main pipeline)
# ──────────────────────────────────────────────

def relevance_mask(series: pd.Series, terms: List[str]) -> pd.Series:
    ordered = sorted({t.lower().strip() for t in terms if t and t.strip()}, key=len, reverse=True)
    pattern = "|".join([rf"(?<!\w){re.escape(t)}(?!\w)" for t in ordered])
    return series.str.lower().str.contains(pattern, regex=True, na=False)


def construction_relevance_mask(df_sent: pd.DataFrame, cfg: Dict) -> pd.Series:
    text_series = (df_sent["title"].fillna("") + " " + df_sent["sentence"].fillna("")).str.lower()
    infra  = relevance_mask(text_series, cfg.get("infrastructure_terms", []))
    risk   = relevance_mask(text_series, cfg.get("risk_terms", []))
    proc   = relevance_mask(text_series, cfg.get("construction_process_terms", []))
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
            rows.append({"source": "The Observer", "url": r["url"],
                         "publication_date": r["publication_date"],
                         "title": r["title"], "sentence": sent})
    return pd.DataFrame(rows)


def train_content_classifier(df_sent: pd.DataFrame) -> Pipeline:
    pipe = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=30000, stop_words="english")),
        ("clf", LinearSVC(class_weight="balanced", random_state=42, max_iter=3000)),
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


def semantic_predict(sentences: List[str], prototype_map: Dict[str, List[str]]) -> Tuple[List[str], List[float]]:
    labels = list(prototype_map.keys())
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        proto_vecs = {l: np.mean(model.encode(prototype_map[l], normalize_embeddings=True), axis=0) for l in labels}
        sent_vecs = model.encode(sentences, normalize_embeddings=True)
        pred_labels, confs = [], []
        for vec in sent_vecs:
            sims = {l: float(np.dot(vec, proto_vecs[l])) for l in labels}
            best = max(sims, key=sims.get)
            pred_labels.append(best); confs.append(sims[best])
        return pred_labels, confs
    except Exception:
        corpus = sentences[:]
        for l in labels:
            corpus.extend(prototype_map[l])
        tfidf = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", min_df=1)
        mat = tfidf.fit_transform(corpus)
        n = len(sentences)
        sent_mat = mat[:n]
        proto_mats, idx = {}, n
        for l in labels:
            m = len(prototype_map[l])
            proto_mats[l] = mat[idx:idx + m].mean(axis=0)
            idx += m
        pred_labels, confs = [], []
        for i in range(n):
            sims = {l: float(cosine_similarity(sent_mat[i], proto_mats[l])[0, 0]) for l in labels}
            best = max(sims, key=sims.get)
            pred_labels.append(best); confs.append(sims[best])
        return pred_labels, confs


def fuse_predictions(content_label, semantic_label, content_conf, semantic_conf):
    if content_label == semantic_label:
        return content_label, min(0.99, 0.5 + 0.25 * content_conf + 0.25 * semantic_conf), "agree"
    if semantic_conf - content_conf > 0.20:
        return semantic_label, max(0.50, semantic_conf), "semantic_override"
    return content_label, max(0.50, content_conf), "content_preferred"


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",     required=True)
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year",   type=int, default=2026)
    parser.add_argument("--per-year",   type=int, default=150,
                        help="Max articles to download per calendar year")
    parser.add_argument("--output-dir", default="outputs_observer_full")
    parser.add_argument("--seed",       type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    os.makedirs(args.output_dir, exist_ok=True)
    cfg = json.load(open(args.config, encoding="utf-8"))

    observer_cfg = next((s for s in cfg["sources"] if "observer" in s["name"].lower()), None)
    allowed_url_keywords = [k.lower() for k in (observer_cfg.get("allowed_url_keywords", []) if observer_cfg else [])]
    sleep = cfg.get("request_sleep_seconds", 1.2)

    # ── Stage 1: collect all sitemap URLs and stratify ──
    print("=== Stage 1: scanning Observer sitemaps ===")
    all_pairs = get_all_observer_urls(args.start_year, args.end_year, sleep)
    print(f"Total candidate URLs in {args.start_year}–{args.end_year}: {len(all_pairs)}")

    print("\nStratified sampling:")
    sample = stratified_sample(all_pairs, args.per_year, allowed_url_keywords)
    print(f"Total to download: {len(sample)}")

    # ── Stage 2: download and parse articles ──
    print("\n=== Stage 2: downloading articles ===")
    start_date = f"{args.start_year}-01-01"
    end_date   = f"{args.end_year}-12-31"
    rows = []
    for i, (url, feed_date) in enumerate(sample, 1):
        if i % 20 == 0:
            print(f"  {i}/{len(sample)} downloaded…")
        html = fetch_url(url)
        if not html:
            continue
        parsed = parse_article(html, url, feed_date)
        if not parsed.get("text") or len(parsed["text"]) < 300:
            continue
        rows.append({"source": "The Observer", **parsed})
        time.sleep(sleep)

    df_articles = pd.DataFrame(rows)
    if df_articles.empty:
        print("No articles parsed.")
        return
    df_articles = df_articles.drop_duplicates(subset=["url"]).reset_index(drop=True)
    df_articles.to_csv(os.path.join(args.output_dir, "articles_raw.csv"), index=False)
    print(f"\nArticles downloaded: {len(df_articles)}")
    print(df_articles.assign(year=pd.to_datetime(df_articles['publication_date'],errors='coerce').dt.year)['year'].value_counts().sort_index().to_string())

    # ── Stage 3: sentence segmentation ──
    print("\n=== Stage 3: sentence segmentation ===")
    sent_df = build_sentence_table(df_articles)
    print(f"Sentences: {len(sent_df)}")

    # ── Stage 4: relevance filter ──
    print("\n=== Stage 4: relevance filter ===")
    sent_df["is_relevant"] = construction_relevance_mask(sent_df, cfg)
    sent_df = sent_df[sent_df["is_relevant"]].copy()
    print(f"Relevant sentences: {len(sent_df)}")
    if sent_df.empty:
        print("No relevant sentences. Adjust vocabulary in config.")
        return

    filtered_articles = df_articles[df_articles["url"].isin(sent_df["url"].unique())].copy()
    filtered_articles.to_csv(os.path.join(args.output_dir, "articles_collected.csv"), index=False)

    # ── Stage 5: content classification ──
    print("\n=== Stage 5: content classification ===")
    sent_df["label_content"] = sent_df["sentence"].apply(lambda s: weak_label(s, cfg["category_keywords"]))
    train_df = sent_df.dropna(subset=["label_content"]).copy()
    if train_df.empty or train_df["label_content"].nunique() < 2:
        bootstrap = [{"sentence": ex, "label_content": lbl}
                     for lbl, exs in cfg["semantic_prototypes"].items() for ex in exs]
        train_df = pd.DataFrame(bootstrap)
    content_model = train_content_classifier(train_df[["sentence", "label_content"]])
    sent_df["pred_content"] = content_model.predict(sent_df["sentence"])
    try:
        d = content_model.decision_function(sent_df["sentence"])
        conf = np.abs(d) if d.ndim == 1 else np.max(np.abs(d), axis=1)
        conf = (conf - conf.min()) / (conf.max() - conf.min() + 1e-9)
        sent_df["conf_content"] = conf
    except Exception:
        sent_df["conf_content"] = 0.60

    # ── Stage 6: semantic classification ──
    print("\n=== Stage 6: semantic classification ===")
    sem_labels, sem_confs = semantic_predict(sent_df["sentence"].tolist(), cfg["semantic_prototypes"])
    sent_df["pred_semantic"] = sem_labels
    sent_df["conf_semantic"] = sem_confs

    # ── Stage 7: fusion ──
    fused = sent_df.apply(
        lambda r: fuse_predictions(r["pred_content"], r["pred_semantic"],
                                   float(r["conf_content"]), float(r["conf_semantic"])), axis=1)
    sent_df[["pred_final", "conf_final", "fusion_rule"]] = pd.DataFrame(fused.tolist(), index=sent_df.index)

    # ── Stage 8: monthly aggregation ──
    sent_df["month"] = sent_df["publication_date"].apply(lambda d: d[:7] if d and len(d) >= 7 else None)
    agg = (sent_df.groupby(["month", "source", "pred_final"], dropna=True)
           .size().reset_index(name="count")
           .sort_values(["month", "count"], ascending=[True, False]))

    # ── Stage 9: outputs ──
    sent_df.to_csv(os.path.join(args.output_dir, "sentences_classified.csv"), index=False)
    agg.to_csv(os.path.join(args.output_dir, "monthly_dispute_signals.csv"), index=False)

    summary = {
        "source": "The Observer",
        "year_range": f"{args.start_year}–{args.end_year}",
        "articles_raw": int(len(df_articles)),
        "articles_collected": int(len(filtered_articles)),
        "relevant_sentences": int(len(sent_df)),
        "final_categories": sorted(sent_df["pred_final"].dropna().unique().tolist()),
        "articles_per_year": df_articles.assign(
            year=pd.to_datetime(df_articles["publication_date"], errors="coerce").dt.year
        )["year"].value_counts().sort_index().to_dict(),
        "collected_per_year": filtered_articles.assign(
            year=pd.to_datetime(filtered_articles["publication_date"], errors="coerce").dt.year
        )["year"].value_counts().sort_index().to_dict(),
    }
    with open(os.path.join(args.output_dir, "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\n=== Done ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
