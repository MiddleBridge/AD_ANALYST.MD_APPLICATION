# VC Fund Screening & Ops Agent

## What this is

This project is an AI-native workflow artifact for a VC fund Analyst / Sourcing & Ops scope.
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

## How to run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# add keys
python main.py --once
```

## Known limitations

See `KNOWN_LIMITATIONS.md`.
