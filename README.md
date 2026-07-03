# The Observer

Research and analysis workspace for newspaper discourse and dispute-signal modeling, focused on Observer corpus pipelines and downstream validation/visualization outputs.

## Project Overview

This repository contains:

- Corpus collection and filtering pipelines
- Dispute-signal extraction and sentence classification workflows
- Extended actor/spatial/network analysis scripts
- Validation and visualization generators for publication-ready outputs
- Draft manuscript and chapter materials

## Repository Layout

- `paper2_online_discourse/` — core scripts, research writeups, and generated datasets/figures
- `paper2_online_discourse/outputs*` — run-specific output directories (raw/collected articles, monthly signals, classified sentences, summaries)
- `paper2_online_discourse/outputs_observer_full_corpus/` — full-corpus integrated outputs, tables, and extended analysis artifacts

## Quick Start

1. Clone the repository.
2. Create and activate a Python environment.
3. Install required dependencies used by the scripts.
4. Run the target pipeline script from `paper2_online_discourse/`.

Example script entry points:

- `paper2_online_discourse/observer_full_corpus_2015_2025.py`
- `paper2_online_discourse/pipeline_newspaper_disputes.py`
- `paper2_online_discourse/observer_historical_pipeline.py`
- `paper2_online_discourse/generate_observer_visualizations.py`

## Key Artifacts

- `articles_raw.csv`, `articles_collected.csv`
- `monthly_dispute_signals.csv`
- `sentences_classified.csv`
- `run_summary.json`
- analysis tables and publication figures under full-corpus output folders

## Notes

- Some output directories are large and may change frequently between runs.
- Keep run metadata (`run_summary.json`) for reproducibility and audit trails.
- Script naming reflects research-stage workflows; prefer documenting run parameters in commit messages.

## License

Add the preferred license for publication/distribution before sharing publicly.
