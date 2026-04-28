# ARCHITECTURE — Inovo.VC AI Screening Agent

> Jedno miejsce z pełnym opisem systemu — jak działa, dlaczego tak, co gdzie leży.

---

## Po co ten system istnieje

Inbound pitch decków to chaos. Tygodniowo wpływa kilkadziesiąt emaili z PDF-ami,
z których 80% odpada w 30 sekund lektury — zła geografia, zły stage, nie startup.
Ale te 30 sekund trzeba poświęcić każdemu z osobna.

**Cel:** zamienić chaos inboundu w uporządkowany pipeline.
Skrócić screening z godzin do minut.
Zostawić człowiekowi tylko decyzje, których AI nie powinna podejmować samodzielnie.

---

## Trzy zasady projektu

1. **Human-in-the-loop przy każdej komunikacji z founderem.**
   AI nigdy nie wysyła emaila samodzielnie — zawsze tworzy draft do zatwierdzenia.

2. **Kill switch na każdym etapie.**
   80% dealów odpada na Gate 1 (3 sekundy, $0.006). Tylko ~20% widzi deck analysis.

3. **Pełna transparentność.**
   Każdy wyciągnięty Markdown z PDF-a trafia do `logs/`. Wiesz dokładnie co AI czytało.

---

## Architektura — przegląd

```
Gmail (dokuzu12@gmail.com only)
        │
        ▼
┌──────────────────────────────────────────────────┐
│  PRE-FILTER (deterministic, 0 tokenów)           │
│  Blokuje: NDA, kontrakty, faktury, non-pitch     │
│  Wymaga: słów kluczowych pitch/deck/raising      │
└──────────────────┬───────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────┐
│  GATE 1 — Quick Fit (~3 sek, ~$0.006)            │
│  Model: GPT-4o (gpt-4o, max 512 output tokens)  │
│  Input: treść emaila — BEZ PDF                   │
│                                                  │
│  Verdict:                                        │
│  PASS             → kontynuuj                    │
│  UNCERTAIN_READ_DECK → pobierz deck, kontynuuj   │
│  FAIL_CONFIDENT   → stop, oznacz w Gmail        │
│                                                  │
│  Sprawdza: CEE geografia, Pre-Seed/Seed stage,   │
│  właściwy sektor, real startup (nie agency)      │
└──────────────────┬───────────────────────────────┘
                   │ PASS / UNCERTAIN
                   ▼
┌──────────────────────────────────────────────────┐
│  PDF → MARKDOWN (lokalnie, 0 tokenów, ~2 sek)   │
│  pymupdf4llm konwertuje PDF → tekst              │
│  Limit: 60 000 znaków (~15k tokenów)             │
│  Output: logs/<nazwa>_extracted.md              │
└──────────────────┬───────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────┐
│  GATE 2 — Deck Analysis (3 wywołania LLM)        │
│                                                  │
│  2A — EXTRACT (~5 sek, ~$0.03)                   │
│       Model: GPT-4o                              │
│       Wyciąga: founderzy + background, geo,      │
│       traction metrics, ask, model biznesowy     │
│       Output: structured JSON facts              │
│                                                  │
│  2B — SCORE (~10 sek, ~$0.05)                    │
│       Model: GPT-4o                              │
│       Input: facts JSON (nie raw deck)           │
│       Scoruje 11 wymiarów VC (1-10) z reasoning  │
│       i evidence cytowanym z facts               │
│                                                  │
│  2C — BRIEF (~5 sek, ~$0.02)                     │
│       Model: GPT-4o (light)                      │
│       Pisze executive summary, venture scale,    │
│       top 3 strengths/concerns, rekomendację     │
│                                                  │
│  Overall score = weighted average (traction 1.5x,│
│  founder-market-fit 1.3x, timing 1.2x)          │
│  Próg: ≥ 6.0 → PASS                             │
└──────────────────┬───────────────────────────────┘
                   │ score < 6.0 → stop
                   │ score ≥ 6.0 → brief dla człowieka
                   ▼
┌──────────────────────────────────────────────────┐
│  GATE 2.5 — External research (opcjonalnie)       │
│                                                  │
│  Input: fakty + wymiary + kontekst gate1/gate2    │
│  Output: external_score + sources + risk_penalty  │
│  Zapis: gate25_* w SQLite + external_opportunity  │
│                                                  │
│  Provider: Tavily (opcjonalnie) lub LLM-only      │
└──────────────────┬───────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────┐
│  GATE 3 — Human Review (HITL)                    │
│  Interface: terminal (Rich UI)                   │
│  Brak tokenów, brak kosztu                       │
│                                                  │
│  Brief pokazuje:                                 │
│  • Firma: co robią, jak, dla kogo                │
│  • Founderzy z backgroundem                      │
│  • Scorecard 11 wymiarów z reasoning             │
│  • Brakujące info + pytania do foundera          │
│  • Red flags (solution-love / slow execution)    │
│  • Venture scale assessment                      │
│                                                  │
│  Decyzja: A / R / S                              │
└──────────────────┬───────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────┐
│  ORCHESTRATION                                   │
│  APPROVE → draft email z propozycją calla        │
│  REJECT  → draft rejection z konkretnym powodem │
│  (oba trafiają do Gmail Drafts — nie wysyłają się│
│  automatycznie)                                  │
└──────────────────────────────────────────────────┘
```

---

## Scoring — 11 wymiarów VC

Framework oparty na power law thinking: szukamy outlierów, nie dobrych biznesów.

Dwa root causes upadku startupów mapują wszystkie red flagi:
1. **Zakochanie się w rozwiązaniu zamiast w problemie** → solution-love flags
2. **Za wolne tempo** → slow execution flags

| Wymiar | Waga | Co mierzy |
|--------|------|-----------|
| Timing / Why Now | 1.2× | Inflection point — dlaczego teraz? |
| Problem | 1.0× | Ból realny, częsty, z budżetem |
| Wedge | 1.0× | Ostry punkt wejścia, szybki time-to-value |
| Founder-Market Fit | 1.3× | Unfair right to win |
| Product Love | 1.0× | Kto byłby wściekły gdyby produkt zniknął? |
| Execution Speed | 1.0× | Shipping pace, founder robi sales? |
| Market | 1.0× | Venture-scale? Może zwrócić fundusz? |
| Moat Path | 1.0× | Co się kompounduje w czasie? |
| Traction | 1.5× | Konkretne liczby z decku (MRR, users, growth) |
| Business Model | 1.0× | Monetyzacja klarowna, unit economics |
| Distribution | 1.5× | Kanały akwizycji, PLG/outbound, CAC proxy |

```
Overall = (sum of score × weight) / sum(weights)
Próg Gate 2: ≥ 6.0
```

Każdy wymiar ma: **score** (1-10) + **reasoning** (cytat/dowód z decku).

---

## Stack technologiczny

| Warstwa | Technologia | Plik |
|---------|-------------|------|
| AI — scoring + extraction | GPT-4o (OpenAI API) | `agents/screener.py` |
| PDF → Markdown | pymupdf4llm (lokalnie) | `tools/pdf_utils.py` |
| Gmail polling + drafts | Gmail API OAuth2 | `tools/gmail_client.py` |
| Pipeline storage | SQLite | `storage/database.py` |
| HITL interface | Terminal + Rich | `hitl/terminal.py` |
| Email drafts | Gmail Drafts API | `agents/orchestrator.py` |
| Weekly report | Python (no LLM) | `agents/reporter.py` |
| Website screening | GPT-4o + crawling | `agents/website_screener.py` |

---

## Zabezpieczenia kosztowe

| Zapora | Limit | Działanie po przekroczeniu |
|--------|-------|---------------------------|
| Allowed sender | `ALLOWED_SENDER` env | Pozostałe maile ignorowane |
| Pre-filter (deterministic) | brak tokenów | Blokuje legal docs, non-pitch |
| PDF size | `MAX_PDF_MB=20` | Błąd, deal pomijany |
| Markdown length | `MAX_MARKDOWN_CHARS=60000` | Obcięcie z adnotacją |
| Output tokens Gate 1 | 512 | Hard limit w API call |
| Output tokens Gate 2 | 2048 per stage | Hard limit w API call |
| Dzienny limit dealów | `DAILY_DEAL_LIMIT=50` | Cap na jedną sesję |

**Szacowany koszt:** ~$0.09 per deal (Gate1 + Gate2 z deckiem).
**Tygodniowo przy 50 inboundach:** ~$2-5.

---

## Struktura plików

```
INOVO_AI/
│
├── main.py                      # Entry point — CLI, polling loop
├── SYSTEM_ARCHITECTURE.md       # Ten dokument
├── PRODUCT_REQUIREMENTS.md      # Product requirements
├── requirements.txt
├── .env                         # Konfiguracja (nie commitować)
├── credentials.json             # Gmail OAuth credentials
├── token.json                   # Gmail OAuth token (auto)
│
├── agents/
│   ├── screener.py              # Gate 1 + Gate 2 (3-stage pipeline)
│   ├── orchestrator.py          # Draft emaili po decyzji HITL
│   ├── reporter.py              # Tygodniowy raport (no LLM)
│   ├── website_screener.py      # Website-only screening
│   ├── schemas.py               # Pydantic schemas (Gate2 outputs)
│   ├── quality_checks.py        # Post-LLM quality checks
│   └── [inne]                   # Moduły pomocnicze
│
├── config/
│   ├── prompts.py               # Wszystkie system+user prompty
│   ├── criteria.py              # Kryteria inwestycyjne Inovo
│   ├── scoring.py               # Wzory scoring + wagi wymiarów
│   └── llm_cost.py              # Nazwy modeli, limity tokenów
│
├── hitl/
│   └── terminal.py              # Rich UI — 1-pager brief + decyzja
│
├── tools/
│   ├── gmail_client.py          # Gmail API: fetch, draft, label
│   └── pdf_utils.py             # PDF → Markdown (pymupdf4llm)
│
├── storage/
│   ├── models.py                # Dataclasses: EmailData, Gate1/2Result, Brief
│   ├── database.py              # SQLite pipeline state machine
│   └── pipeline.db              # Lokalny plik bazy danych
│
├── logs/
│   └── *_extracted.md           # Wyciągnięty Markdown z każdego PDF
│
└── _archive/
    └── agent/                   # Stara równoległa architektura (nieaktywna)
```

---

## Jak uruchomić

```bash
# 1. Setup (raz)
cd /Users/adrian/INOVO_AI
source venv/bin/activate
pip install -r requirements.txt
python setup_gmail.py           # OAuth z Google

# 2. Uzupełnij .env
OPENAI_API_KEY=sk-proj-...
ALLOWED_SENDER=dokuzu12@gmail.com

# 3. Test na lokalnym PDF
python main.py --test path/to/deck.pdf

# 4. Jednorazowy scan skrzynki (sprawdza co jest, potem kończy)
python main.py --once

# 5. Ciągły polling co 15 min
python main.py

# 6. Raport tygodniowy
python main.py --report

# 7. Rescan konkretnego dealu
python main.py --rescan <message_id>

# 8. Interaktywny pick + rescan z listy
python main.py --pick

# 9. Screening przez URL strony (bez emaila)
python main.py assess-url https://example.com
```

---

## Zmienne środowiskowe (.env)

| Zmienna | Default | Opis |
|---------|---------|------|
| `OPENAI_API_KEY` | — | **Wymagane** |
| `GMAIL_CREDENTIALS_PATH` | `credentials.json` | OAuth credentials |
| `GMAIL_TOKEN_PATH` | `token.json` | OAuth token (auto) |
| `GMAIL_USER_EMAIL` | — | Twój email |
| `ALLOWED_SENDER` | `dokuzu12@gmail.com` | Jedyny skanowany sender |
| `REVIEWER_NAME` | `Adrian` | Imię w emailach |
| `CALENDLY_LINK` | — | Link do bookingu |
| `POLLING_INTERVAL_MINUTES` | `15` | Jak często sprawdza Gmail |
| `GATE2_PASS_THRESHOLD` | `6.0` | Minimalny score Gate 2 |
| `MAX_MARKDOWN_CHARS` | `60000` | Max długość deck markdown |
| `MAX_PDF_MB` | `20` | Max rozmiar PDF |
| `DAILY_DEAL_LIMIT` | `50` | Max dealów dziennie |
| `HITL_MODE` | `skip` | `interactive` = terminal HITL |
| `SCREENING_DEPTH` | `INITIAL` | `INITIAL` / `ENRICHED` |
| `ENABLE_EXTERNAL_CHECK` | `1` | Gate 2.5 (wymaga depth=ENRICHED) |

---

## Co system robi a czego NIE robi

### ✅ Robi
- Skanuje skrzynkę i filtruje sender (tylko `ALLOWED_SENDER`)
- Blokuje legal docs zanim dotknie je AI (pre-filter)
- Odrzuca oczywiste niefity w 3 sekundy (Gate 1)
- Konwertuje PDF na Markdown lokalnie — pełna transparentność
- Wyciąga metadata: founderzy, traction, ask, model biznesowy
- Scoruje 11 wymiarów VC z reasoning cytującym deck
- Wykrywa red flags (solution-love, slow execution)
- Wyświetla czysty 1-pager brief w terminalu
- Tworzy draft emaila (approve/reject) w Gmail Drafts
- Loguje wyciągnięty Markdown do `logs/`
- Przechowuje historię w SQLite z pełnym audit trail

### ❌ NIE robi
- **Nie wysyła emaili samodzielnie** — zawsze draft do zatwierdzenia
- **Nie podejmuje decyzji inwestycyjnych** — asystent, nie decydent
- **Nie przeszukuje LinkedIn/Crunchbase** (brak web search w domyślnym trybie)
- **Nie weryfikuje liczb** z decku — cytuje je, nie sprawdza

---

## Kalibracja scoringu — benchmarki z portfolio

System kalibrowany na znanych spółkach Inovo:

| Spółka | Oczekiwany score | Kluczowe sygnały |
|--------|-----------------|-----------------|
| Pathway | 9/10 | AI infra, real-time, tech moat, global |
| Booksy | 9/10 | Network effects, marketplace, global category |
| Spacelift | 8/10 | DevOps wedge, PLG, Polish founders |
| Pythagora | 8/10 | AI coding, clear why-now, traction |
| Splx.ai | 8/10 | LLM security, CEE diaspora, enterprise pain |
| Infermedica | 8/10 | Healthcare AI, clinical moat, CEE |
| Sintra.ai | 7/10 | AI agents, SMB, fast growth |
| Index Health | 3/10 | Wrong geo fit, no moat → FAILED |

Jeśli test na Splx.ai daje < 7.0 — scoring jest źle skalibrowany.

---

## Typowy koszt per deal

| Scenariusz | Czas | Koszt |
|-----------|------|-------|
| Odpada na Gate 1 | 3 sek | ~$0.006 |
| Odpada na Gate 2 (z deckiem) | 25 sek | ~$0.09 |
| Przechodzi do HITL | 25 sek + Ty | ~$0.09 |
| 50 emaili/tydzień (80% fail G1) | — | ~$2-4/tydzień |
