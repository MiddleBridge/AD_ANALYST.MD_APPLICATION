# Inovo Analyst Application — Read This First

This repository is an application artifact tailored for **Analyst (Sourcing & Ops)**.
It demonstrates three concrete, high-value workflows:

1. **VC screening inbound** (email/deck + website-only)
2. **Pipeline management** (Notion sync + weekly reporting)
3. **Meeting notes** (one-shot call sync to company page + optional tasks DB)

If you only read two files:
- `INOVO_AI_opis_dzialania.txt` — concise 3-use-case narrative
- `main.py` — executable entrypoint and CLI surface

## What is implemented

- End-to-end screening pipeline with staged gates and HITL decision point.
- Idempotent pipeline persistence in `pipeline.db`.
- Notion sync for deal pages (`agents/notion_sync.py`).
- Weekly ops report CLI (`python main.py --report --days 7`).
- One-shot call notes sync to Notion (`python main.py sync-call ...`) in `agents/call_sync.py`.

## Quick demo commands

```bash
python main.py --once
python main.py assess-url https://example.com
python main.py --report --days 7
python main.py --sync-notion --days 30
python main.py sync-call --company "RiskSeal" --call-id "ff_demo_001" --source fireflies --title "Founder intro" --url "https://app.fireflies.ai/view/..." --date "2026-04-28" --attendees "Partner, Founder" --summary "GTM + ICP discussed" --tasks "Request deck metrics;Schedule follow-up"
```

## Structure

- `main.py` — CLI/orchestration
- `agents/` — screening, scoring, reporting, notion sync, call sync
- `tools/` — Gmail/PDF/website integrations
- `storage/` — models and SQLite persistence
- `config/` — prompts and scoring configuration
- `tests/` — unit tests

## Notes

- Secrets are intentionally excluded (`.env`, OAuth tokens, local DB).
- Non-essential archives/debug artifacts were removed to keep this submission focused.
