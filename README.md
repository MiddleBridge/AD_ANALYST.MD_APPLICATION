# VC Fund Screening & Ops Agent

## What this is

This project is an AI-native workflow artifact for a VC fund Analyst / Sourcing & Ops scope.
It is an end-to-end operating system that handles the full chain: from the moment an inbound message appears in the inbox, through analysis (email/deck/website), to downstream pipeline ownership (briefs, next-step recommendations, meeting notes, and follow-ups).
It screens inbound founder emails, decks, and company websites.
It converts noisy inputs into decision-useful company briefs.
It supports pipeline hygiene, reporting, and next-step tracking.
It keeps human approval in the loop for sensitive actions.
It is designed as an application artifact: clear outputs, explicit limits, and safe defaults.

## Why this matters for an Analyst / Sourcing & Ops role

- Partner leverage via faster first-pass screening and cleaner briefs.
- Founder pipeline support with structured records and clear next actions.
- Decision-useful synthesis from messy input sources.
- Reusable templates/checklists for notes and follow-ups.
- AI-native workflows with explicit safety boundaries and HITL controls.

## Core workflows

### 1. Inbound founder screening

- Email/deck intake and pre-filtering.
- Initial fund-fit classification.
- Deck or website analysis and scorecard generation.
- Fund-fit output with explicit risks/missing data.
- Recommended next action for partner review.

### 2. Website/company screening

- Website extraction and structured fact parsing.
- Scoring via a configurable rubric.
- Missing-data and confidence handling.
- Decision-ready summary for quick partner triage.

### 3. Pipeline, notes, and reporting

- Structured company records and status transitions.
- Notion-style sync for pipeline memory.
- Weekly reporting for ops cadence.
- Meeting-note sync and next-step tracking.

## Demo outputs

- `examples/01_sample_company_screen.md`
- `examples/02_sample_website_screen.md`
- `examples/03_sample_weekly_pipeline_report.md`

## Safety principles

- No autonomous investment decisions.
- No automatic founder email sending.
- Human approval required for sensitive actions.
- Credentials stay outside the repository.
- Missing data is always shown explicitly.
- Scores are decision support, not final truth.

## Local setup (run on your machine)

### 1) Requirements

- Python 3.9+
- `pip`
- Gmail OAuth credentials (optional, only for inbox/deck flow)
- OpenAI API key (required for screening)
- Notion API key + database (optional, for sync)

### 2) Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 3) Configure `.env` (minimum)

Required:
- `OPENAI_API_KEY`

If you want Gmail intake:
- `GMAIL_CREDENTIALS_PATH`
- `GMAIL_TOKEN_PATH`
- `GMAIL_USER_EMAIL`
- `ALLOWED_SENDER`

If you want Notion sync:
- `NOTION_API_KEY`
- `NOTION_DATABASE_ID`

### 4) First-time Gmail auth (optional)

```bash
python setup_gmail.py
```

### 5) Run common flows

```bash
# Process inbox once
python main.py --once

# Website-only screen
python main.py assess-url https://example.com

# Weekly pipeline report (no new LLM calls)
python main.py --report --days 7

# Sync recent records to Notion
python main.py --sync-notion --days 30

# Add a founder call note to a company page in Notion
python main.py sync-call --company "ExampleCo" --call-id "ff_demo_001" --source fireflies --title "Founder intro" --url "https://app.fireflies.ai/view/..." --date "2026-04-29" --attendees "Partner, Founder" --summary "Discussion summary" --tasks "Request metrics;Schedule follow-up"
```

## Integrations and tech stack

- OpenAI API: classification, extraction, scoring, summaries
- Gmail API (OAuth): inbound email/deck intake + draft handling
- Notion API: pipeline memory, deal pages, call notes, task sync
- SQLite: local pipeline state and reporting data
- Python CLI: orchestration in `main.py`

## Known limitations

See `KNOWN_LIMITATIONS.md`.
