#!/usr/bin/env python3
"""
Pipeline: Ugandan newspaper text mining for construction dispute risk signals.

Stages
A) Acquire article links and article text
B) Clean and sentence-segment
C) Infrastructure/dispute relevance filter
D) Content classification (TF-IDF + LinearSVC)
E) Semantic classification (SentenceTransformer centroid matching; fallback available)
F) Fusion
G) Monthly aggregation
H) Export outputs

Usage:
  python pipeline_newspaper_disputes.py \
    --config scrape_config_paper2.json \
    --start-date 2015-01-01 \
    --end-date 2025-12-31 \
    --max-articles-per-source 150 \
    --output-dir outputs
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC


# -----------------------------
# Utility
# -----------------------------

def to_iso_date(value: str) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    for fmt in ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"]:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    # fallback: try first 10 chars if it looks like ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    return None


def in_date_range(date_iso: Optional[str], start: str, end: str) -> bool:
    if not date_iso:
        return False
    return start <= date_iso <= end


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def sent_split(text: str) -> List[str]:
    text = normalize_text(text)
    if not text:
        return []
    # simple sentence segmentation for reproducibility without external models
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    return [p.strip() for p in parts if len(p.strip()) > 20]


# -----------------------------
# Data acquisition
# -----------------------------

@dataclass
class SourceConfig:
    name: str
    base_url: str
    feed_urls: List[str]
    article_selector: str
    date_selector: str
    title_selector: str


def load_config(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_url(url: str, timeout: int = 20) -> Optional[str]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            return r.text
    except requests.RequestException:
        return None
    return None


def extract_links_from_feed(feed_xml: str) -> List[Tuple[str, Optional[str]]]:
    links: List[Tuple[str, Optional[str]]] = []
    soup = BeautifulSoup(feed_xml, "xml")
    for item in soup.find_all(["item", "entry"]):
        link = None
        pub = None
        link_tag = item.find("link")
        if link_tag:
            # RSS <link>text</link> or Atom <link href="..."/>
            link = link_tag.get("href") or link_tag.text
        pub_tag = item.find("pubDate") or item.find("published") or item.find("updated")
        if pub_tag:
            pub = pub_tag.text
        if link:
            links.append((link.strip(), to_iso_date(pub or "")))
    return links


def extract_sitemap_urls(sitemap_xml: str) -> List[Tuple[str, Optional[str]]]:
    links: List[Tuple[str, Optional[str]]] = []
    soup = BeautifulSoup(sitemap_xml, "xml")
    for url_tag in soup.find_all("url"):
        loc_tag = url_tag.find("loc")
        if not loc_tag or not loc_tag.text:
            continue
        loc = loc_tag.text.strip()
        lastmod_tag = url_tag.find("lastmod")
        lastmod = to_iso_date((lastmod_tag.text or "").strip()) if lastmod_tag else None
        links.append((loc, lastmod))
    return links


def extract_child_sitemaps(sitemap_index_xml: str) -> List[str]:
    out: List[str] = []
    soup = BeautifulSoup(sitemap_index_xml, "xml")
    for sm in soup.find_all("sitemap"):
        loc_tag = sm.find("loc")
        if loc_tag and loc_tag.text:
            out.append(loc_tag.text.strip())
    return out


def extract_links_from_html(html: str, base_url: str) -> List[Tuple[str, Optional[str]]]:
    links: List[Tuple[str, Optional[str]]] = []
    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href:
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if parsed.scheme not in {"http", "https"}:
            continue

        path = parsed.path.lower()
        segments = [seg for seg in path.split("/") if seg]
        if any(token in path for token in ["/tag/", "/author/", "/topic/", "/topics/", "/search", "/video", "/tv", "/podcast", "/ondemand"]):
            continue

        last = segments[-1] if segments else ""
        if last in {"news", "business", "politics", "sports", "world", "education"}:
            continue

        article_like = False
        if len(segments) >= 2 and segments[0] in {"news", "business", "technology", "education", "viewpoint", "sports"}:
            article_like = True
        elif len(segments) >= 3 and segments[0] == "category" and segments[1] in {"news", "business", "politics", "world", "education", "sports", "science", "entertainment"}:
            article_like = True
        elif len(segments) >= 2 and segments[0] == "opportunities":
            article_like = True

        if not article_like and re.search(r"(?:-nv_\d+_\d+|-\d{4,}|_[0-9]{5,})", last, flags=re.I):
            article_like = True

        if article_like:
            links.append((full, None))

    # preserve order, drop duplicates
    seen = set()
    deduped = []
    for link, dt in links:
        if link in seen:
            continue
        seen.add(link)
        deduped.append((link, dt))
    return deduped


def parse_article_html(html: str, source: SourceConfig, url: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if source.title_selector:
        el = soup.select_one(source.title_selector)
        if el:
            title = normalize_text(el.get_text(" ", strip=True))
    if not title:
        h1 = soup.find("h1")
        title = normalize_text(h1.get_text(" ", strip=True)) if h1 else ""

    date_txt = ""
    if source.date_selector:
        el = soup.select_one(source.date_selector)
        if el:
            date_txt = normalize_text(el.get_text(" ", strip=True))
    if not date_txt:
        m = soup.find("meta", attrs={"property": "article:published_time"})
        if m and m.get("content"):
            date_txt = m.get("content")

    article_text = ""
    if source.article_selector:
        body = soup.select_one(source.article_selector)
        if body:
            paras = [normalize_text(p.get_text(" ", strip=True)) for p in body.find_all("p")]
            article_text = " ".join([p for p in paras if p])
    if not article_text:
        paras = [normalize_text(p.get_text(" ", strip=True)) for p in soup.find_all("p")]
        article_text = " ".join([p for p in paras if p])

    return {
        "url": url,
        "title": title,
        "publication_date": to_iso_date(date_txt),
        "text": normalize_text(article_text),
    }


def collect_articles(cfg: Dict, start_date: str, end_date: str, max_articles_per_source: int) -> pd.DataFrame:
    rows = []
    for s in cfg["sources"]:
        source = SourceConfig(
            name=s["name"],
            base_url=s["base_url"],
            feed_urls=s.get("feed_urls", []),
            article_selector=s.get("article_selector", ""),
            date_selector=s.get("date_selector", ""),
            title_selector=s.get("title_selector", "h1"),
        )
        allowed_url_keywords = [k.lower() for k in s.get("allowed_url_keywords", [])]

        candidates: List[Tuple[str, Optional[str]]] = []
        for feed in source.feed_urls:
            payload = fetch_url(feed)
            if not payload:
                continue
            if "<rss" in payload.lower() or "<feed" in payload.lower() or "<item>" in payload.lower():
                candidates.extend(extract_links_from_feed(payload))
            elif "<sitemapindex" in payload.lower():
                for sm_url in extract_child_sitemaps(payload):
                    sm_payload = fetch_url(sm_url)
                    if not sm_payload:
                        continue
                    if "<urlset" in sm_payload.lower() and "<loc" in sm_payload.lower():
                        candidates.extend(extract_sitemap_urls(sm_payload))
                    time.sleep(cfg.get("request_sleep_seconds", 1.0))
            elif "<urlset" in payload.lower() and "<loc" in payload.lower():
                candidates.extend(extract_sitemap_urls(payload))
            else:
                candidates.extend(extract_links_from_html(payload, source.base_url))
            time.sleep(cfg.get("request_sleep_seconds", 1.0))

        seen = set()
        kept = 0
        for link, feed_date in candidates:
            if link in seen:
                continue
            seen.add(link)

            if allowed_url_keywords and not any(k in link.lower() for k in allowed_url_keywords):
                continue

            if feed_date and not in_date_range(feed_date, start_date, end_date):
                continue

            html = fetch_url(link)
            if not html:
                continue
            parsed = parse_article_html(html, source, link)
            parsed_date = parsed.get("publication_date") or feed_date

            if parsed_date and not in_date_range(parsed_date, start_date, end_date):
                continue
            if not parsed.get("text") or len(parsed["text"]) < 300:
                continue

            rows.append(
                {
                    "source": source.name,
                    "url": link,
                    "title": parsed.get("title", ""),
                    "publication_date": parsed_date,
                    "text": parsed.get("text", ""),
                }
            )
            kept += 1
            if kept >= max_articles_per_source:
                break
            time.sleep(cfg.get("request_sleep_seconds", 1.0))

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["url"]).reset_index(drop=True)
    df["title_norm"] = df["title"].str.lower().str.replace(r"\s+", " ", regex=True).str.strip()
    df = df.drop_duplicates(subset=["source", "title_norm"]).drop(columns=["title_norm"]).reset_index(drop=True)
    return df


# -----------------------------
# Relevance + weak labels
# -----------------------------

def build_sentence_table(df_articles: pd.DataFrame) -> pd.DataFrame:
    out = []
    for _, r in df_articles.iterrows():
        for sent in sent_split(r["text"]):
            out.append(
                {
                    "source": r["source"],
                    "url": r["url"],
                    "publication_date": r["publication_date"],
                    "title": r["title"],
                    "sentence": sent,
                }
            )
    return pd.DataFrame(out)


def relevance_mask(series: pd.Series, terms: List[str]) -> pd.Series:
    ordered_terms = sorted({t.lower().strip() for t in terms if t and t.strip()}, key=len, reverse=True)
    pattern = "|".join([rf"(?<!\w){re.escape(t)}(?!\w)" for t in ordered_terms])
    return series.str.lower().str.contains(pattern, regex=True, na=False)


def construction_relevance_mask(df_sent: pd.DataFrame, cfg: Dict) -> pd.Series:
    """
    Broadened relevance filter: Return sentences that match infrastructure/process OR risk terms.
    This allows catching dispute-relevant articles even if they lack both infra AND risk signals.
    """
    text_series = (df_sent["title"].fillna("") + " " + df_sent["sentence"].fillna("")).str.lower()

    infrastructure_terms = cfg.get("infrastructure_terms", cfg.get("relevance_terms", []))
    risk_terms = cfg.get("risk_terms", [])
    process_terms = cfg.get("construction_process_terms", [])

    infra_mask = relevance_mask(text_series, infrastructure_terms)
    risk_mask = relevance_mask(text_series, risk_terms) if risk_terms else pd.Series(False, index=df_sent.index)
    process_mask = relevance_mask(text_series, process_terms) if process_terms else pd.Series(False, index=df_sent.index)
    
    # Return sentences that have (infra AND (risk OR process)) OR (infrastructure without risk/process but from known construction articles)
    # For now, use: infra matches OR (risk matches without needing infra) OR (process without needing infra)
    return infra_mask | risk_mask | process_mask


def weak_label(sentence: str, category_keywords: Dict[str, List[str]]) -> Optional[str]:
    s = sentence.lower()
    scores = {}
    for cat, kws in category_keywords.items():
        scores[cat] = sum(1 for k in kws if k in s)
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return None
    return best


# -----------------------------
# Content classification (TF-IDF + SVM)
# -----------------------------

def train_content_classifier(df_sent: pd.DataFrame) -> Pipeline:
    pipe = Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=30000, stop_words="english")),
            ("clf", LinearSVC(class_weight="balanced", random_state=42, max_iter=3000)),
        ]
    )
    X = df_sent["sentence"]
    y = df_sent["label_content"]
    # train/test split retained for optional diagnostics when the dataset is large enough
    label_counts = y.value_counts(dropna=True)
    can_stratify = not label_counts.empty and int(label_counts.min()) >= 2
    if len(df_sent) >= max(20, y.nunique() * 4) and can_stratify:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.25, random_state=42, stratify=y
        )
        pipe.fit(X_train, y_train)
    else:
        pipe.fit(X, y)
    return pipe


# -----------------------------
# Semantic classification
# -----------------------------

def semantic_predict(
    sentences: List[str],
    prototype_map: Dict[str, List[str]],
) -> Tuple[List[str], List[float]]:
    """
    Returns semantic labels + confidence scores.
    Tries SentenceTransformer; falls back to TF-IDF centroid similarity.
    """
    labels = list(prototype_map.keys())

    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2")
        proto_vecs = {}
        for label in labels:
            vecs = model.encode(prototype_map[label], normalize_embeddings=True)
            proto_vecs[label] = np.mean(vecs, axis=0)

        sent_vecs = model.encode(sentences, normalize_embeddings=True)
        pred_labels, confs = [], []
        for vec in sent_vecs:
            sims = {lab: float(np.dot(vec, proto_vecs[lab])) for lab in labels}
            best_lab = max(sims, key=sims.get)
            pred_labels.append(best_lab)
            confs.append(sims[best_lab])
        return pred_labels, confs

    except Exception:
        # Fallback: TF-IDF centroid matching
        corpus = sentences[:]
        for label in labels:
            corpus.extend(prototype_map[label])

        tfidf = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", min_df=1)
        mat = tfidf.fit_transform(corpus)

        n_sent = len(sentences)
        sent_mat = mat[:n_sent]

        proto_mats = {}
        idx = n_sent
        for label in labels:
            m = len(prototype_map[label])
            proto_mats[label] = mat[idx : idx + m].mean(axis=0)
            idx += m

        pred_labels, confs = [], []
        for i in range(n_sent):
            sims = {}
            for label in labels:
                sims[label] = float(cosine_similarity(sent_mat[i], proto_mats[label])[0, 0])
            best_lab = max(sims, key=sims.get)
            pred_labels.append(best_lab)
            confs.append(sims[best_lab])
        return pred_labels, confs


# -----------------------------
# Fusion + aggregation
# -----------------------------

def fuse_predictions(content_label: str, semantic_label: str, content_conf: float, semantic_conf: float) -> Tuple[str, float, str]:
    if content_label == semantic_label:
        return content_label, min(0.99, 0.5 + 0.25 * content_conf + 0.25 * semantic_conf), "agree"

    # conservative resolution: prefer content label unless semantic confidence is much higher
    if semantic_conf - content_conf > 0.20:
        return semantic_label, max(0.50, semantic_conf), "semantic_override"
    return content_label, max(0.50, content_conf), "content_preferred"


def month_key(date_iso: Optional[str]) -> Optional[str]:
    if not date_iso or len(date_iso) < 7:
        return None
    return date_iso[:7]


# -----------------------------
# Main
# -----------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--max-articles-per-source", type=int, default=150)
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--labeled-sentences-csv", default="")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    cfg = load_config(args.config)

    # A) collect
    articles = collect_articles(cfg, args.start_date, args.end_date, args.max_articles_per_source)
    if articles.empty:
        print("No articles collected. Check feeds/selectors/date window.")
        return
    articles.to_csv(os.path.join(args.output_dir, "articles_raw.csv"), index=False)

    # B) sentence table
    sent_df = build_sentence_table(articles)
    if sent_df.empty:
        print("No sentences extracted.")
        return

    # C) relevance filter
    sent_df["is_relevant"] = construction_relevance_mask(sent_df, cfg)
    sent_df = sent_df[sent_df["is_relevant"]].copy()
    if sent_df.empty:
        print("No relevant sentences after filtering.")
        return

    filtered_articles = articles[articles["url"].isin(sent_df["url"].unique())].copy()
    filtered_articles.to_csv(os.path.join(args.output_dir, "articles_collected.csv"), index=False)

    # D) content labels
    if args.labeled_sentences_csv and os.path.exists(args.labeled_sentences_csv):
        lbl = pd.read_csv(args.labeled_sentences_csv)
        # expected columns: sentence,label
        train_df = lbl.dropna(subset=["sentence", "label"]).copy()
        train_df = train_df.rename(columns={"label": "label_content"})
    else:
        # weak supervision bootstrap
        sent_df["label_content"] = sent_df["sentence"].apply(lambda s: weak_label(s, cfg["category_keywords"]))
        train_df = sent_df.dropna(subset=["label_content"]).copy()

    if train_df.empty or train_df["label_content"].nunique() < 2:
        bootstrap_rows = []
        for label, examples in cfg["semantic_prototypes"].items():
            for sent in examples:
                bootstrap_rows.append({"sentence": sent, "label_content": label})
        train_df = pd.DataFrame(bootstrap_rows)

    if train_df.empty or train_df["label_content"].nunique() < 2:
        print("Insufficient labeled data for content classifier.")
        return

    content_model = train_content_classifier(train_df[["sentence", "label_content"]])

    sent_df["pred_content"] = content_model.predict(sent_df["sentence"])
    # distance to hyperplane as pseudo-confidence (normalized)
    try:
        d = content_model.decision_function(sent_df["sentence"])
        if d.ndim == 1:
            conf = np.abs(d)
        else:
            conf = np.max(np.abs(d), axis=1)
        conf = (conf - conf.min()) / (conf.max() - conf.min() + 1e-9)
        sent_df["conf_content"] = conf
    except Exception:
        sent_df["conf_content"] = 0.60

    # E) semantic predictions
    prototype_map = cfg["semantic_prototypes"]
    sem_labels, sem_confs = semantic_predict(sent_df["sentence"].tolist(), prototype_map)
    sent_df["pred_semantic"] = sem_labels
    sent_df["conf_semantic"] = sem_confs

    # F) fusion
    fused = sent_df.apply(
        lambda r: fuse_predictions(
            r["pred_content"],
            r["pred_semantic"],
            float(r["conf_content"]),
            float(r["conf_semantic"]),
        ),
        axis=1,
    )
    sent_df[["pred_final", "conf_final", "fusion_rule"]] = pd.DataFrame(fused.tolist(), index=sent_df.index)

    # G) monthly aggregation
    sent_df["month"] = sent_df["publication_date"].apply(month_key)
    agg = (
        sent_df.groupby(["month", "source", "pred_final"], dropna=True)
        .size()
        .reset_index(name="count")
        .sort_values(["month", "source", "count"], ascending=[True, True, False])
    )

    # H) outputs
    sent_df.to_csv(os.path.join(args.output_dir, "sentences_classified.csv"), index=False)
    agg.to_csv(os.path.join(args.output_dir, "monthly_dispute_signals.csv"), index=False)

    summary = {
        "articles_collected": int(len(filtered_articles)),
        "articles_raw": int(len(articles)),
        "relevant_sentences": int(len(sent_df)),
        "final_categories": sorted(sent_df["pred_final"].dropna().unique().tolist()),
        "date_range": {"start": args.start_date, "end": args.end_date},
    }
    with open(os.path.join(args.output_dir, "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("Done.")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
