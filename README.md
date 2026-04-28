# INOVO AI Screening Agent

Automatyczny pipeline do preselekcji inbound startupów dla Inovo:
- `email + deck` (Gmail + PDF),
- `website-only` (bez maila),
- zapis do `pipeline.db`,
- opcjonalny sync do Notion,
- opcjonalny krok HITL (human-in-the-loop).

Projekt jest oparty o etapowe bramki (Gate 1 / Gate 2 / Gate 2.5 / Gate 3), żeby ograniczać koszt i nie robić drogich analiz tam, gdzie nie ma sensu.

## 1) Jak to jest zbudowane

### Główne katalogi
- `main.py` - CLI i orchestracja całego pipeline.
- `agents/` - logika screeningowa (Gate 1/2/2.5, website flow, scoring, Notion sync, raporty).
- `tools/` - integracje techniczne (Gmail API, PDF extraction/OCR, website->markdown).
- `storage/` - modele danych i zapis do `pipeline.db` (SQLite).
- `config/` - prompty i konfiguracje scoring/cost.
- `tests/` - testy jednostkowe (m.in. scoring i website screening).

### Kluczowe moduły
- `agents/screener.py` - klasyczny flow email+deck (Gate 1 + Gate 2).
- `agents/website_screener.py` - flow website-only.
- `agents/external_check.py` + `agents/final_scoring.py` - Gate 2.5 (zewnętrzne wzbogacenie + finalny score).
- `agents/notion_sync.py` - push/sync rekordów do Notion DB.
- `storage/database.py` - statusy pipeline i metody zapisu poszczególnych etapów.

## 2) Przepływ działania (email + deck)

### Gate 0 - prefilter (tani, deterministiczny)
W `main.py` (`_should_run_ai_on_email`) działa szybki filtr:
- blokuje dokumenty legal/confidential (NDA/MSA/DPA/SOW itd.),
- przepuszcza tylko maile wyglądające jak pitch/fundraising.

Jeśli nie przejdzie: deal dostaje status `SKIPPED`, mail trafia do manualnego review (bez LLM).

### Gate 1 - fit do mandatu Inovo
`screener.gate1_fit_check(...)` klasyfikuje:
- `PASS`,
- `UNCERTAIN_READ_DECK`,
- `FAIL_CONFIDENT`.

Dodatkowo jest heurystyka ryzyka nadawcy (`_assess_sender_authority`) i sygnał `auth_risk`.

**Ważna zasada:** przy `FAIL_CONFIDENT` pipeline domyślnie kończy się `STOP`, chyba że włączony override (`DEBUG_OVERRIDE`, `MANUAL_OVERRIDE`) albo uruchamiasz `--rescan`.

### Gate 2 - analiza decka
Jeśli jest PDF:
- pobranie attachmentu,
- ekstrakcja markdown (`tools/pdf_utils.py`),
- kontrola jakości ekstrakcji (`SKIP_GATE2_ON_BAD_DECK` może zatrzymać flow),
- scoring i strukturyzacja wyniku.

Na tym etapie zapisują się m.in.:
- `deck_evidence_score`,
- decyzje rozdzielone: `innovo_fit_decision`, `deck_evidence_decision`, `generic_vc_interest`,
- `final_action`.

### Gate 2.5 - external enrichment (opcjonalny)
Uruchamia się tylko gdy:
- głębokość to `ENRICHED` lub `DEEP_DIVE`,
- trigger przejdzie (fit + auth risk + minimalny score),
- `ENABLE_EXTERNAL_CHECK=1`.

Wynik:
- `external_score`,
- kara ryzyka i hard cap,
- finalny score i finalny werdykt.

### Gate 3 - HITL (opcjonalny)
- `HITL_MODE=interactive` -> terminalowa decyzja partnera + drafty maili.
- `HITL_MODE=skip` -> brak blokowania; pipeline kończy się statusem oczekującym.

## 3) Przepływ website-only (`assess-url`)

Komenda:
```bash
python main.py assess-url https://example.com
```

Flow:
- crawl i ekstrakcja treści strony,
- facts + scoring website,
- opcjonalny Gate 2.5 (zewnętrzny),
- zapis jako pełnoprawny rekord do `pipeline.db` (`source_type=website`),
- opcjonalny sync do Notion (`NOTION_AUTO_SYNC=1`).

Dla błędów website scan też powstaje rekord z `WEBSITE_SCAN_ERROR`, żeby nic nie znikało z pipeline.

## 4) Statusy i dane w SQLite

Baza: `pipeline.db`, tabela `deals`.

Przykładowe statusy (`storage/database.py`):
- `NEW`,
- `GATE1_RUNNING`, `GATE1_PASSED`, `GATE1_FAILED`,
- `GATE2_RUNNING`, `GATE2_FAILED`,
- `WAITING_HITL`,
- `REJECTED_GATE1`, `REJECTED_GATE2`, `REJECTED_EXTERNAL_CHECK`,
- `APPROVED_DRAFT_CREATED`, `REJECTED_DRAFT_CREATED`,
- `SKIPPED`, `ERROR`.

Dodatkowo trzymane są metryki:
- tokeny i koszt per etap (`*_input_tokens`, `*_output_tokens`, `*_cost_usd`),
- czasy (`*_started_at`, `*_finished_at`, `*_latency_ms`),
- decyzje screeningowe i składowe scoringu.

## 5) CLI - najważniejsze komendy

### Start/polling
```bash
python main.py
```
Ciągły loop Gmail z interwałem `POLLING_INTERVAL_MINUTES`.

### Jednorazowe przetworzenie obecnych maili
```bash
python main.py --once
```

### Test lokalnego PDF (bez Gmail)
```bash
python main.py --test "/sciezka/do/deck.pdf"
```

### Rescan po `message_id`
```bash
python main.py --rescan <MESSAGE_ID>
```

### Interaktywny wybór deala do rescanu
```bash
python main.py --pick
```

### Raport
```bash
python main.py --report --days 7
```

### Sync do Notion
```bash
python main.py --sync-notion --days 30
```

### OAuth setup dla Gmail
```bash
python main.py --setup
```

## 6) Konfiguracja (`.env`)

Minimalnie potrzebne:
- `OPENAI_API_KEY`
- `GMAIL_CREDENTIALS_PATH`
- `GMAIL_TOKEN_PATH`
- `GMAIL_USER_EMAIL`
- `GMAIL_PROCESSED_LABEL`

Przydatne klucze operacyjne:
- `POLLING_INTERVAL_MINUTES` - co ile minut loop sprawdza nowe maile.
- `GATE2_PASS_THRESHOLD` - próg zaliczenia Gate 2.
- `SCREENING_DEPTH` - `INITIAL` / `ENRICHED` / `DEEP_DIVE`.
- `ENABLE_EXTERNAL_CHECK` - czy uruchamiać Gate 2.5.
- `HITL_MODE` - `skip` albo `interactive`.
- `NOTION_AUTO_SYNC` - auto push do Notion.
- `NOTION_AUTO_SYNC_MODE` - `per_deal` albo `batch`.

Uwaga porządkowa:
- nie duplikuj tych samych kluczy w `.env` (ostatnia wartość nadpisuje poprzednie),
- trzymaj `POLLING_INTERVAL_MINUTES` na sensownym poziomie (np. `15`) jeśli nie potrzebujesz agresywnego polling.

## 7) Uruchomienie lokalne

### Wymagania
- Python 3.9+
- Tesseract OCR (dla OCR PDF)
- konto Gmail z OAuth credentials

### Instalacja
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Następnie uzupełnij `.env` i odpal:
```bash
python main.py --setup
python main.py --once
```

## 8) Proces w tle (launchd na macOS)

Jeśli uruchamiasz przez LaunchAgent (`com.inovo.screening`), proces może działać stale w tle.

Szybkie komendy:
```bash
launchctl list | grep -i inovo
pgrep -fl "INOVO_AI/main.py"
```

Wyłączenie:
```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.inovo.screening.plist
```

Żeby nie startował po zalogowaniu, usuń/zmień nazwę pliku `.plist`.

## 9) Testy

```bash
pytest -q
```

Aktualnie są testy m.in. dla:
- `tests/test_final_scoring.py`
- `tests/test_website_screening.py`

## 10) Bezpieczeństwo i operacyjne good practices

- Nigdy nie commituj sekretów z `.env` (`OPENAI_API_KEY`, `NOTION_API_KEY`, tokeny OAuth).
- Po przypadkowym ujawnieniu klucza - zrób rotację.
- Trzymaj logi i `pipeline.db` poza publicznym repo.
- Przy zmianach promptów/scoringu warto robić porównanie na stałym zestawie benchmark cases (`--rescan`).

---

## 11) Skills agenta (capabilities)

Poniżej lista praktycznych "umiejętności" Twojego agenta w obecnej implementacji.

### A) Intake i klasyfikacja wejścia
- rozpoznaje źródło: `email+deck` albo `website-only`,
- parsuje dane wejściowe do wspólnego modelu (`EmailData`),
- odrzuca wcześnie treści nie-pitchowe i potencjalnie poufne (prefilter Gate 0).

### B) Screening mandatowy (Inovo fit)
- klasyfikuje geografia/stage/sektor (Gate 1),
- daje werdykt `PASS` / `UNCERTAIN_READ_DECK` / `FAIL_CONFIDENT`,
- ocenia ryzyko autorytetu nadawcy i dokłada sygnały do decyzji.

### C) Analiza decka i scoring
- pobiera PDF z Gmail lub bierze lokalny PDF (`--test`),
- robi ekstrakcję markdown + OCR fallback,
- ocenia jakość ekstrakcji i może zatrzymać flow przy słabym decku,
- tworzy scoring wielowymiarowy + strengths/risks + missing data.

### D) External enrichment i finalny werdykt
- uruchamia Gate 2.5 warunkowo (tylko gdy są spełnione triggery),
- łączy `internal_deck_score` z `external_score`,
- stosuje risk penalty / hard cap,
- wylicza finalny werdykt i rekomendowaną akcję.

### E) Outputy decyzyjne i ścieżki akcji
- rozdziela decyzje na: `innovo_fit_decision`, `deck_evidence_decision`, `generic_vc_interest`,
- zapisuje `final_action` (np. `STOP`, `ASK_FOR_MORE_INFO`, `PASS_TO_PARTNER`, `TEST_CASE_ONLY`),
- wspiera HITL (`interactive`) i tryb automatyczny (`skip`).

### F) Persistence i operacje
- zapisuje każdy etap do `pipeline.db` (w tym błędy),
- wspiera `rescan` po `message_id` i interaktywny `--pick`,
- drukuje telemetry token/cost per etap,
- opcjonalnie synchronizuje rekordy do Notion.

## 12) Module map (co robi każdy moduł)

### `main.py`
- punkt wejścia CLI,
- orchestracja gate'ów,
- loop Gmail + polling,
- website mode (`assess-url`),
- raport token/cost i finalny status.

### `agents/`
- `screener.py` - Gate 1 + Gate 2 dla email/deck.
- `website_screener.py` - Gate 1 + scoring dla website-only.
- `external_check.py` - zbieranie sygnałów zewnętrznych (Gate 2.5).
- `final_scoring.py` - blending score, penalty, hard cap, final verdict.
- `notion_sync.py` - sync do Notion (batch i per-deal).
- `reporter.py` - raporty pipeline.
- `vc_snapshot.py` - krótka karta "VC Snapshot" z evidence.
- `website_vc_pipeline.py` / `website_vc_pack.py` / `website_vc_facts_digest.py` - logika wzbogacenia website flow.
- `website_vc_llm.py` - wywołania LLM i telemetry dla website VC pack.
- `schemas.py` / `schemas_website.py` / `schemas_website_vc.py` / `schemas_gate25.py` - modele danych i walidacja.
- `deck_rubric_caps.py` - limity/capy dla rubryki deckowej.
- moduły scoringowe (`market_reality.py`, `retention_model.py`, `right_to_win.py`, `competition_density.py`, `distribution_engine.py`, `unit_economics.py`, `outlier_filter.py`, `competitive_intelligence.py`, `trend_analysis.py`) - analizy cząstkowe używane w scoringu i ocenie jakości startupu.

### `tools/`
- `gmail_client.py` - Gmail API (czytanie wiadomości, attachmenty, etykiety, drafty),
- `pdf_utils.py` - ekstrakcja decka do markdown + OCR,
- `website_to_markdown.py` - pobranie i konwersja stron WWW.

### `storage/`
- `database.py` - schema/migracje SQLite, status machine, save/update etapów,
- `models.py` - dataclass models (EmailData, Gate1Result, Gate2Result itd.).

### `config/`
- `prompts.py`, `website_prompts.py` - prompty LLM,
- `scoring.py`, `website_scoring.py` - reguły punktacji i werdyktów,
- `llm_cost.py` - kalkulacja kosztów tokenów.

## 13) Decision tree (jak agent podejmuje decyzje)

1. **Intake**  
   Wejście z Gmail / PDF test / website URL.

2. **Prefilter (Gate 0)**  
   Jeśli legal/confidential albo non-pitch -> `SKIPPED`.

3. **Gate 1 (fit)**  
   - `FAIL_CONFIDENT` -> domyślnie `STOP`.  
   - `PASS` lub `UNCERTAIN_READ_DECK` -> przejście dalej.

4. **Gate 2 (deck evidence)**  
   - deck unreadable -> `PDF_EXTRACTION_FAILED` / manual review, stop,  
   - score < progu -> `deck_evidence_decision=NEEDS_MORE_INFO` i zwykle stop/ask,  
   - score >= progu -> możliwe przejście do Gate 2.5 lub HITL.

5. **Gate 2.5 (opcjonalny external)**  
   Włącza się tylko przy odpowiedniej głębokości i triggerach.

6. **Final action**  
   Na podstawie split decisions i score:
   - `STOP`,
   - `ASK_FOR_MORE_INFO`,
   - `PASS_TO_PARTNER`,
   - `TEST_CASE_ONLY` (benchmark/debug override).

7. **HITL (opcjonalny)**  
   - `interactive`: manualna decyzja + draft maila,
   - `skip`: zapis statusu i zamknięcie etapu automatycznie.

## 14) Co dziala realnie vs co jest heurystyczne

### Stabilne (production-grade w tym repo)
- End-to-end flow `email+deck` i `assess-url` z zapisem do `pipeline.db`.
- Staged decisions (`innovo_fit_decision`, `deck_evidence_decision`, `generic_vc_interest`, `final_action`).
- Hard stop na `FAIL_CONFIDENT` bez override.
- Notion upsert po `Message ID` (idempotent update/create).
- Token/cost telemetry drukowane per scan.

### Heurystyczne / kompromisy MVP
- Sender authority check (LinkedIn URL + heurystyki domeny) to sygnal pomocniczy, nie twarda weryfikacja tozsamosci.
- Gate 0 prefilter (legal/non-pitch) jest regułowy i może dać false positive/false negative.
- Ocena quality deck extraction zależy od OCR i jakości PDF.
- Trigger wejścia do Gate 2.5 jest progowy (rule-based), nie adaptacyjny.

### Placeholdery / ograniczenia, które warto znać
- Nie wszystkie pola historycznych rekordów mają nowy split decisions (stare wiersze mogą mieć `NULL`).
- Rekordy historyczne sprzed fixów mogą mieć legacy `message_id` (np. `website_unknown-site`).
- `source_type` istnieje w modelu aplikacji, ale może nie istnieć w starszym schemacie lokalnej bazy dopóki migracja nie doda tej kolumny.
- Tytuł Notion z prefiksem score używa score z pipeline; jeśli rekord historycznie nie ma score, prefiks pozostaje `[--]`.

## 15) Aktualny stan po ostatnich fixach

### Notion
- Tytuł `Company` jest czystą nazwą firmy (bez prefiksu score).
- Score jest trzymany wyłącznie w kolumnie `Score` (Number 0-10).
- `sync` działa idempotentnie po `Message ID` (update istniejącego rekordu, create jeśli brak).
- Strona deala ma zarządzany układ 9 sekcji (18 bloków `heading_2 + paragraph`) i jest utrzymywana przez `agents/notion_sync.py`:
  - `⚡ Decision Snapshot`
  - `📋 Deal Summary`
  - `🎯 Screening Result`
  - `💪 Strengths`
  - `⚠️ Risks`
  - `🏁 Market Context`
  - `❓ Missing & Follow-ups`
  - `➡️ Recommended Next Step`
  - `🔍 Raw Notes`
- Sync aktualizuje treść sekcji deterministycznie z `pipeline.db`; nie woła dodatkowego LLMa na etapie Notion sync.
- `🔍 Raw Notes` jest chronione: ręczne notatki partnera nie są nadpisywane.

### Duplikaty i legacy rekordy
- Duplikaty mogą pojawić się historycznie po zmianie sposobu budowy `message_id` (np. stare `website_unknown-site`).
- Legacy rekordy warto archiwizować w Notion po `Message ID`, zamiast usuwać dane z `pipeline.db`.

### Notion table UX (stan docelowy)
- Operacyjna tabela jest uproszczona do: `Company`, `Score`, `Status`, `Sector`, `Received At` (+ techniczne `Message ID` do idempotentnego upsertu).
- `Status` to Select: `Reject`, `Review`, `Pass`, `TEST_CASE`.
- `Received At` jest zapisywane jako Notion `date` (format dnia, bez czasu).

### Rekomendowane ustawienia `.env` (operacyjnie)
- `POLLING_INTERVAL_MINUTES=15` (zamiast bardzo agresywnego `1`).
- `RUN_GATE2_ON_GATE1_FAIL=0` dla twardego STOP po `FAIL_CONFIDENT`.
- Jedna wartość na klucz (bez duplikatów `NOTION_DATABASE_ID`, `PDF_OCR_MODE`, `TESSERACT_LANG`).

### Kiedy `[--]` w Notion jest poprawne
- Gdy rekord naprawdę nie ma score (np. zatrzymał się na Gate 1 / błąd przed Gate 2).
- To nie jest błąd UI, tylko sygnał, że analiza punktowa nie została wykonana.
