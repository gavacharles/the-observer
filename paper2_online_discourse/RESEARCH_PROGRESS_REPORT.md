# Research Progress Report
## Paper 2: Temporal Analysis of Construction Dispute Risk Signals in Ugandan Online News (2015–2025)

**Prepared:** April 13, 2026  
**Pipeline:** `pipeline_newspaper_disputes.py` + `observer_historical_pipeline.py` + `observer_full_corpus_2015_2025.py`  
**Working Directory:** `output/results/paper2_online_discourse/`

---

## 1. Executive Summary

This report documents the development, execution, and current status of the text-mining pipeline for Paper 2 of the Temporal Analysis Research project. The pipeline is designed to extract and classify construction dispute risk signals from Ugandan online news sources across the period 2015–2025.

Three target news sources were identified: **The Observer**, **New Vision**, and **Daily Monitor**. Of these, only The Observer has proven fully accessible via automated scraping. The Observer corpus has been successfully built through three successive pipeline runs, culminating in a full-corpus scrape launched on 13 April 2026 targeting all 5,796 construction-relevant articles from 2015–2025.

Key achievements to date:

| Metric | Value |
|--------|-------|
| Observer articles scraped (full run in progress) | 5,796 target URLs |
| Observer articles analysed (stratified pilot) | 844 |
| Relevant (construction) articles (pilot) | 404 (47.9%) |
| Classified sentences (pilot) | 1,642 |
| Dispute categories identified | 7 |
| Years covered | 2016–2025 (2015 not in sitemaps) |
| New Vision historical access | ❌ Blocked (Nuxt SPA) |
| Daily Monitor access | ❌ Blocked (HTTP 403) |

---

## 2. Research Context

### 2.1 Research Objectives

Paper 2 seeks to:
1. Identify construction project dispute risk signals from Ugandan online news discourse.
2. Track temporal trends in those signals across a 10-year window (2015–2025).
3. Complement the Auditor General (OAG) reports analysed in Paper 1 with an independent, real-time media perspective.
4. Build a labelled sentence corpus for semantic classification of dispute categories.

### 2.2 Seven Dispute Categories

The classification taxonomy, developed from the OAG analysis and literature, comprises:

| Category | Description |
|----------|-------------|
| `procurement_irregularity` | Non-competitive tendering, bid rigging, PPDA violations |
| `delay_time_overrun` | Project delays, contractor stalling, extension of time claims |
| `payment_financial_dispute` | Unpaid certificates, retention disputes, contractor insolvency |
| `land_row_dispute` | Compensation disputes, evictions, boundary conflicts |
| `contract_management_failure` | Scope changes, poor supervision, variation order abuse |
| `governance_oversight_failure` | Parliamentary censure, audit findings, ministerial accountability |
| `quality_technical_defect` | Shoddy workmanship, substandard materials, structural failures |

---

## 3. Pipeline Architecture

The text-mining pipeline is implemented across three Python scripts and operates in eight stages:

```
Stage A: URL Collection (sitemap crawl)
    ↓
Stage B: URL Pre-filter (slug keyword matching)
    ↓
Stage C: Article Download & Parse
    ↓
Stage D: Sentence Segmentation
    ↓
Stage E: Vocabulary Relevance Filter (infra | risk | process terms)
    ↓
Stage F: Keyword Weak-Labelling + TF-IDF/LinearSVC Content Classifier
    ↓
Stage G: Semantic Classifier (sentence-transformers / TF-IDF centroid fallback)
    ↓
Stage H: Prediction Fusion + Monthly Aggregation + Export
```

### 3.1 Stage A – URL Collection

The Observer publishes a `sitemap_index.xml` containing 33 `post-sitemap*.xml` child files. Each child sitemap contains `<url>` entries with `<loc>` (article URL) and `<lastmod>` (publication date) fields. The pipeline:
1. Fetches the sitemap index.
2. Iterates all 33 child sitemaps.
3. Retains only entries whose `lastmod` date falls within the target year range.

**Result:** 32,117 candidate URLs for 2015–2025 (2016–2025 in practice; 2015 had 0 archived entries).

### 3.2 Stage B – URL Pre-filter

Downloading all 32,117 articles would require approximately 10–12 hours (at 1 request/second). A broad keyword pre-filter applied to URL slugs reduces the candidate set to articles that are *a priori* likely to discuss construction, infrastructure, or related disputes. The filter uses 75 terms across four groups:

- **Physical infrastructure:** road, bridge, dam, hospital, school, pipeline, railway, airport, etc.
- **Project/contract terms:** construct, contractor, contract, tender, procurement, bid, works, renovation, etc.
- **Dispute/oversight terms:** corrupt, fraud, audit, parliament, investigate, delay, overrun, dispute, payment, etc.
- **Ugandan agencies:** UNRA, KCCA, NWSC, UMEME, PPDA, OAG, etc.

**Result:** 5,796 URLs retained (18% of 32,117), estimated download time ~77 minutes at 0.8 s/request.

### 3.3 Stage C – Article Download & Parse

For each URL, the pipeline:
- Issues a `GET` request with a browser-like `User-Agent` header.
- Retries up to 3 times on failure with exponential backoff.
- Handles rate-limiting (HTTP 429) with adaptive sleep.
- Writes a rolling checkpoint CSV every 100 articles (enables resume on interruption).
- Parses the article using BeautifulSoup with multiple selector strategies:
  - Title: `<h1>` tag.
  - Date: `<meta property="article:published_time">`, `<time>`, `<span.date>`, etc.
  - Body: `div[itemprop='articleBody']`, `div.articleBody`, `article`, fallback to all `<p>` tags.
- Discards articles with fewer than 80 words.

### 3.4 Stage D – Sentence Segmentation

Text bodies are split into sentences using a regex boundary detector (splits on `. ` / `! ` / `? ` followed by a capital letter or digit). Sentences shorter than 25 characters are discarded.

### 3.5 Stage E – Relevance Filter

Each sentence is tested against three vocabulary lists drawn from `scrape_config_paper2.json`:
- `infrastructure_terms` (e.g., road, bridge, dam, contractor)
- `risk_terms` (e.g., delay, fraud, dispute, overrun)
- `construction_process_terms` (e.g., tender, certificate, payment, variation)

A sentence is retained if it matches **any** of the three lists (logical OR). Earlier versions used AND logic, which rejected too many valid sentences (see §6.1).

### 3.6 Stage F – Content Classification

1. **Keyword weak-labelling:** Each relevant sentence is assigned an initial label by counting matches against per-category keyword lists (7 categories). The category with the most matches is assigned; sentences with zero matches are left unlabelled.
2. **TF-IDF + LinearSVC classifier:** A TF-IDF vectoriser (1–2 grams, max 40,000 features, sublinear TF) feeds a LinearSVC with balanced class weights. Trained on weakly-labelled sentences; a stratified 80/20 train/test split is used when data are sufficient (minimum 2 members per class).

### 3.7 Stage G – Semantic Classification

Prototype sentences from `scrape_config_paper2.json` (`semantic_prototypes`) define the centroid of each category in embedding space. Two implementations are tried in order of preference:
1. **`sentence-transformers` (all-MiniLM-L6-v2):** Encodes each sentence and prototype into a 384-dimensional embedding; cosine similarity is used to assign the nearest category.
2. **TF-IDF centroid fallback:** When `sentence-transformers` is not available or fails, TF-IDF vectors are computed for all sentences and prototype strings; cosine similarity to the mean prototype vector determines the label.

### 3.8 Stage H – Prediction Fusion

The content classifier label and semantic label are fused:

| Condition | Rule | Outcome |
|-----------|------|---------|
| Both agree | `agree` | Shared label, boosted confidence |
| Semantic conf − content conf > 0.20 | `semantic_override` | Semantic label wins |
| Otherwise | `content_preferred` | Content label retained |

In the pilot run, 71% of sentences used `content_preferred`, 29% reached `agree`, and <1% triggered `semantic_override`.

---

## 4. The Observer — Dataset Results

### 4.1 Sitemap Coverage

The Observer's `sitemap_index.xml` contains 33 post-sitemaps covering all content from 2016 onwards. No 2015 content appears in any sitemap. Total articles indexed: **32,117** for 2016–2025.

| Year | Total URLs | After Keyword Filter |
|------|-----------|---------------------|
| 2016 | 548 | 80 |
| 2017 | 5,931 | 1,132 |
| 2018 | 2,929 | 501 |
| 2019 | 3,086 | 494 |
| 2020 | 3,414 | 540 |
| 2021 | 3,320 | 607 |
| 2022 | 3,401 | 658 |
| 2023 | 3,289 | 604 |
| 2024 | 2,534 | 503 |
| 2025 | 3,665 | 677 |
| **Total** | **32,117** | **5,796** |

### 4.2 Stratified Pilot Run Results (80 articles/year — completed)

A stratified pilot run (`observer_historical_pipeline.py`, `outputs_observer_full/`) was completed to validate the pipeline across all years before launching the full corpus scrape.

**Download summary:**

| Year | Articles Downloaded | Relevant Articles | Relevant Sentences |
|------|--------------------|--------------------|-------------------|
| 2016 | 78 | 51 | 193 |
| 2017 | 79 | 40 | 170 |
| 2018 | 77 | 33 | 65 |
| 2019 | 77 | 35 | 62 |
| 2020 | 75 | 28 | 101 |
| 2021 | 74 | 30 | 197 |
| 2022 | 75 | 38 | 161 |
| 2023 | 83 | 34 | 169 |
| 2024 | 76 | 31 | 135 |
| 2025 | 73 | 41 | 201 |
| **Total** | **844** | **404** | **1,642** |

**Category distribution (pilot):**

| Category | Sentences | Share |
|----------|-----------|-------|
| `land_row_dispute` | 789 | 48.1% |
| `delay_time_overrun` | 364 | 22.2% |
| `procurement_irregularity` | 206 | 12.5% |
| `contract_management_failure` | 140 | 8.5% |
| `governance_oversight_failure` | 98 | 6.0% |
| `quality_technical_defect` | 43 | 2.6% |
| `payment_financial_dispute` | 2 | 0.1% |
| **Total** | **1,642** | 100% |

**Observation:** `land_row_dispute` dominates the corpus, likely reflecting Uganda's active land compensation disputes around infrastructure projects. `payment_financial_dispute` is severely under-represented (2 sentences), suggesting either keyword coverage gaps or that Observer covers this category primarily through indirect framing.

### 4.3 Full Corpus Run (in progress — launched 13 April 2026)

Script: `observer_full_corpus_2015_2025.py`  
Output directory: `outputs_observer_full_corpus/`  
Target: **5,796 construction-relevant articles** from 2016–2025  
Expected completion: ~77 minutes from launch

The full corpus run adds:
- A `semantic_dataset.csv` output — a clean sentence + label table ready for ML training and evaluation.
- Checkpoint-based resumability — the download can be interrupted and restarted without re-downloading already-fetched articles.
- Per-article word count filtering (minimum 80 words, up from the implicit 300-character filter in the pilot).

---

## 5. New Vision — Challenges and Technical Barriers

### 5.1 Source Profile

**New Vision** (`newvision.co.ug`) is Uganda's largest circulation daily newspaper and a primary target for this research. It has been in publication since 1986 and maintains a comprehensive digital archive.

### 5.2 Technical Barrier: Nuxt.js Single-Page Application (SPA)

New Vision's website is built on **Nuxt.js**, a server-side rendering (SSR) framework for Vue.js. This architecture introduces a fundamental barrier to traditional web scraping:

**How the barrier works:**
1. The server delivers an HTML shell with a `<div id="__nuxt">` container.
2. All article content is injected via JavaScript at runtime — specifically through a large `window.__NUXT__` JSON object embedded in a `<script>` tag.
3. The `__NUXT__` object contains hydration data but **does not include historical article content** in a readily parseable structure for older articles.
4. The rendered DOM only becomes complete after the JavaScript bundle has executed.

**Attempted approaches and their outcomes:**

| Approach | Outcome |
|----------|---------|
| Direct `requests.get()` on article URLs | Returns partial HTML shell; no article text in response |
| Parsing `__NUXT_DATA__` script tag | Contains page metadata only; full article body not included |
| New Vision sitemap (`/sitemap.xml`) | Returns only 18 static page URLs (homepage, sections); **no article-level URLs** |
| New Vision archives page (`/archives`) | Returns a JavaScript-rendered calendar; not parseable without a browser |
| Category pages (`/category/news`, `/national`) | Render article previews via JavaScript; raw HTML has no article links |
| RSS feeds | New Vision's RSS feeds only list the most recent 20–50 articles |

**Root cause summary:** Without a headless browser (Playwright, Selenium, Puppeteer), it is not possible to discover or retrieve historical New Vision articles from 2015–2024 via automated HTTP requests. The website was redesigned from a PHP/WordPress architecture to Nuxt.js at some point before 2024, removing backward-compatible static HTML rendering.

### 5.3 Potential Future Approaches

The following strategies could unlock historical New Vision content in future work:

1. **Wayback Machine CDX API** (recommended — free, no authentication):
   ```
   http://web.archive.org/cdx/search/cdx?url=newvision.co.ug/news/*
     &output=json&fl=original,timestamp&from=20150101&to=20251231&limit=5000
   ```
   The Internet Archive may hold static snapshots of New Vision articles from before the Nuxt.js migration.

2. **GDELT Project** (free, no authentication): GDELT indexes Ugandan news articles and provides metadata including URLs, themes, and publication dates. The GDELT 2.0 Event Database covers 2015–present.

3. **Headless browser (Playwright)**: Using `playwright` for Python, a headless Chromium instance could render New Vision pages and extract content. This is significantly slower (5–10 seconds per page) and requires additional infrastructure.

4. **Google Custom Search API** (paid): Can search within `site:newvision.co.ug` with date filters; returns article URLs that can then be fetched via Playwright.

---

## 6. Daily Monitor — Challenges and Technical Barriers

### 6.1 Source Profile

**Daily Monitor** (`monitor.co.ug`) is Uganda's second-largest daily newspaper (Nation Media Group), particularly known for investigative journalism covering governance and procurement. It would be a valuable source for the `governance_oversight_failure` and `procurement_irregularity` categories.

### 6.2 Technical Barrier: HTTP 403 Forbidden

All endpoints of `monitor.co.ug` return **HTTP 403 Forbidden** in response to automated scraping attempts:

| Endpoint Tested | Status Code |
|-----------------|-------------|
| `https://www.monitor.co.ug/` | 403 |
| `https://www.monitor.co.ug/uganda/news` | 403 |
| `https://www.monitor.co.ug/uganda/business` | 403 |
| `https://www.monitor.co.ug/sitemap.xml` | 403 |

**Nature of the blocking:** The blocking is IP-range based and/or User-Agent based at the server/CDN level. The website uses Cloudflare (as evidenced by the 403 response headers), which actively blocks:
- Known datacenter IP ranges
- Headless browser signatures
- High-frequency request patterns
- User-Agent strings that do not match real browser fingerprints

**Attempted mitigations:** Standard browser-mimicking `User-Agent` headers were tested; all returned 403. No further mitigation was attempted as bypassing anti-scraping measures raises ethical and legal concerns.

### 6.3 Implications

The absence of Daily Monitor data means the corpus currently represents only one editorial perspective (The Observer). Daily Monitor's investigative coverage — particularly on procurement and governance failures — would significantly strengthen the `governance_oversight_failure` and `procurement_irregularity` categories, which are currently under-represented relative to `land_row_dispute`.

### 6.4 Potential Future Approaches

1. **Wayback Machine CDX API**: Similar to New Vision, archived Monitor pages may be available without Cloudflare blocking.
2. **`cloudscraper` library**: A Python library specifically designed to bypass Cloudflare's JavaScript challenges. Results are not guaranteed and legality varies by jurisdiction.
3. **Commercial data providers**: MediaCloud, Factiva, or LexisNexis may have licensed Daily Monitor content.
4. **Official API or data sharing request**: Nation Media Group could be approached directly for research access.

---

## 7. Pipeline Evolution and Bug Fixes

### 7.1 Relevance Filter — Over-restriction Bug (Fixed)

**Problem:** The initial relevance filter used AND logic:
```python
return infra & (risk | proc)   # original
```
This required every sentence to contain both an infrastructure term AND either a risk or process term. New Vision articles that discussed risk without explicitly naming infrastructure were rejected. Result: 0 New Vision sentences passed the filter out of 22 articles.

**Fix:** Changed to OR logic:
```python
return infra | risk | proc     # corrected
```
This retains any sentence containing at least one term from any of the three vocabulary groups. The result was a 3× increase in collected sentences.

### 7.2 Stratified Split Crash (Fixed)

**Problem:** `sklearn.model_selection.train_test_split` with `stratify=y` raises `ValueError` if any class has fewer than 2 members. On small or sparse datasets (e.g., a single article for a given category), this caused the pipeline to crash.

**Fix:** Added a guard before the split:
```python
label_counts = y.value_counts(dropna=True)
can_stratify = not label_counts.empty and int(label_counts.min()) >= 2
if len(df_sent) >= max(20, y.nunique() * 4) and can_stratify:
    X_tr, _, y_tr, _ = train_test_split(X, y, test_size=0.2, stratify=y, ...)
else:
    pipe.fit(X, y)   # fall back to full training set
```

### 7.3 Quota Gap in Historical Run (Resolved)

**Problem:** The first attempt to run the main pipeline historically (`pipeline_newspaper_disputes.py`) returned only 2016 and 2026 articles. The pipeline iterated sitemaps sequentially and reached the `max_articles_per_source=220` quota after processing the first sitemap (which happened to cover 2016 articles).

**Fix:** Created a dedicated year-stratified script (`observer_historical_pipeline.py`) that:
1. Collects all sitemap URLs for the target year range first.
2. Groups by year.
3. Samples up to `--per-year` articles per year independently.
4. This guarantees balanced representation across all years regardless of quota.

---

## 8. Output File Inventory

### 8.1 Stratified Pilot Run (`outputs_observer_full/`)

| File | Description |
|------|-------------|
| `articles_raw.csv` | All 844 downloaded articles (title, date, URL, text, word_count) |
| `articles_collected.csv` | 404 articles with at least one relevant sentence |
| `sentences_classified.csv` | 1,642 relevant sentences with content, semantic, and fused labels |
| `monthly_dispute_signals.csv` | Monthly aggregated dispute signal counts by category |
| `observer_combined_list_2016_2026.csv` | All 844 articles listed by date |
| `observer_combined_relevant_list_2016_2026.csv` | 404 relevant articles listed by date |
| `run_summary.json` | Full run statistics in JSON format |

### 8.2 Full Corpus Run (`outputs_observer_full_corpus/`) — in progress

| File | Description |
|------|-------------|
| `all_sitemap_urls.csv` | All 32,117 sitemap URLs with dates |
| `prefiltered_urls.csv` | 5,796 URLs that passed the URL-slug keyword filter |
| `articles_raw.csv` | Checkpoint file — grows during download (currently in progress) |
| `articles_relevant.csv` | Articles with ≥1 relevant sentence (written after download completes) |
| `sentences_classified.csv` | Full sentence-level classification corpus |
| `monthly_dispute_signals.csv` | Monthly aggregated signals |
| `semantic_dataset.csv` | **Clean sentence + label table for ML training** |
| `run_summary.json` | Full run statistics |

---

## 9. Current Status and Next Steps

### 9.1 What Is Running Now

The full corpus scrape (`observer_full_corpus_2015_2025.py`) was launched on 13 April 2026 at approximately 10:20 AM. Progress can be monitored via:

```bash
tail -f outputs_observer_full_corpus_run.log
```

Expected completion: ~77 minutes from launch. The checkpoint file (`articles_raw.csv`) grows in real-time and can be inspected during the run.

### 9.2 Immediate Next Steps

1. **Verify full corpus results** — once the scrape completes, inspect `run_summary.json` and `semantic_dataset.csv` for coverage and category balance.

2. **Address `payment_financial_dispute` under-representation** — expand the keyword list for this category in `scrape_config_paper2.json` (e.g., add: "contractor unpaid", "certificate withheld", "retention money", "insolvency", "liquidated damages").

3. **New Vision via Wayback Machine** — test CDX API to retrieve archived New Vision URLs and supplement the Observer-only corpus with a second editorial voice.

4. **Temporal trend analysis** — once the full corpus is ready, produce year-by-year frequency plots for each of the 7 categories to identify trends that can be triangulated against the OAG findings from Paper 1.

5. **Cross-validation with OAG data** — compare peak dispute signal years in the media corpus against the OAG report findings (2017–2025) to assess convergence/divergence between audit findings and media coverage.

### 9.3 Longer-term Considerations

- **Classifier evaluation:** The current classifier is trained on weakly-labelled data (keyword matching). Evaluation against a manually-labelled gold standard (even 100–200 sentences) would strengthen the paper's methodological claims.
- **Inter-rater reliability:** If manual annotation is used for evaluation, two annotators should label the same subset and report Cohen's κ.
- **Temporal validity:** Some vocabulary terms (e.g., "PPDA", "UNRA") carry strong institutional connotations that may skew classification. Consider domain-adapted embeddings or fine-tuning on Ugandan construction news.

---

## 10. Technical Reference

### 10.1 File Paths

| Script | Location |
|--------|----------|
| Main pipeline | `output/results/paper2_online_discourse/pipeline_newspaper_disputes.py` |
| Stratified historical pipeline | `output/results/paper2_online_discourse/observer_historical_pipeline.py` |
| Full corpus pipeline | `output/results/paper2_online_discourse/observer_full_corpus_2015_2025.py` |
| Scrape config | `output/results/paper2_online_discourse/scrape_config_paper2.json` |
| Run log (full corpus) | `output/results/paper2_online_discourse/outputs_observer_full_corpus_run.log` |

### 10.2 Python Environment

| Item | Value |
|------|-------|
| Python | 3.9.6 |
| Virtual env | `.venv/` (project root) |
| Key packages | requests, beautifulsoup4[xml], pandas, scikit-learn, numpy |
| Optional | sentence-transformers (TF-IDF fallback used when unavailable) |

### 10.3 Running the Full Corpus Pipeline

```bash
# Activate environment
source '.venv/bin/activate'

# Run (from paper2_online_discourse directory)
python observer_full_corpus_2015_2025.py \
  --config scrape_config_paper2.json \
  --start-year 2015 \
  --end-year 2025 \
  --output-dir outputs_observer_full_corpus \
  --sleep 0.8

# Monitor progress
tail -f outputs_observer_full_corpus_run.log

# Resume after interruption (checkpoint auto-detected)
# Simply re-run the same command; already-downloaded articles are skipped.
```

---

*Report generated automatically. For questions, see the pipeline source files listed in §10.1.*
