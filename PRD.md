# Product Requirements Document
## Inovo.VC AI Screening Agent

## Security baseline (safe-by-default)

Poniższe zasady są obowiązkowym baseline dla całego produktu i wszystkich zmian:

- **Safe defaults:** wszystkie ryzykowne akcje (outbound, modyfikacje rekordów, integracje zewnętrzne) domyślnie `OFF`; wymagają jawnego włączenia i pozostawiają ślad audytowy.
- **Logging/monitoring:** każdy etap pipeline emituje ustrukturyzowane logi (`deal_id`, etap, status, czas, błąd), z rozróżnieniem `SUCCESS` / `ERROR`; alerty na błędy krytyczne i brak „cichych porażek”.
- **Dependency hygiene:** zależności pinowane, regularnie aktualizowane, skanowane pod CVE/licencje; nieużywane pakiety usuwane; blokada merge przy krytycznych podatnościach.
- **Secrets:** sekretów nie trzymamy w kodzie ani PRD; tylko przez env/secret manager; maskowanie w logach; rotacja i minimalny zakres uprawnień.
- **Input handling:** wszystkie wejścia (mail, PDF tekst, URL, dane zewnętrzne) walidowane, limitowane rozmiarem i sanityzowane; brak zaufania do inputu użytkownika i treści z internetu.
- **Authentication:** dostęp do systemów operacyjnych i integracji tylko dla uwierzytelnionych tożsamości (service accounts / user accounts), bez współdzielonych kont.
- **Authorization:** zasada least privilege; każde konto i token dostaje tylko niezbędne uprawnienia (np. oddzielnie read/write), z okresowym przeglądem dostępu.

## 1. Purpose of this document

Ten plik jest **kontraktem produktowym**, nie README repo: opisuje *dlaczego* i *co ma być dowiezione*. **Indeks canonical** (appendix) wskazuje pliki jako źródło prawdy — żeby nie dublować dokumentacji technicznej na początku lektury.

Szczegóły modułów i uruchomienie → **[ARCHITECTURE.md](ARCHITECTURE.md)**

### Reguła canonical (PRD vs kod vs implementacja)

- **PRD** — źródło prawdy dla **zamierzonego** zachowania produktu (co ma robić system jako narzędzie screeningowe).
- **Kod** — źródło prawdy dla **bieżącego** zachowania wdrożenia (co faktycznie robi binarka dziś).
- Jeśli PRD i kod się rozjeżdżają: traktuj to jako **lukę produktową** — albo aktualizujesz **kod** do PRD, albo świadomie zmieniasz **PRD** z krótkim uzasadnieniem. Nie wolno „po cichu” uznać kodu za produkt bez oznaczenia (wtedy dopisz krótką notatkę „stan implementacji” w PRD lub ticket).

Szczegóły implementacyjne (np. nazwy funkcji, env, sufity tokenów domyślne) żyją w **`ARCHITECTURE.md`** i plikach wskazanych w appendixie — PRD utrzymuje **kontrakty jakościowe i behawioralne**, nie zastępuje README kompilacji.

---

## 2. Problem

Inbound pitch decków i leadów ze stron to chaos. Partner nie może poświęcić czasu na każdy przypadek od zera.

**Rozwiązanie:** jeden pipeline AI, który z materiału (deck **albo** strona WWW) robi ustrukturyzowany screening, zapisuje wynik w bazie i synchronizuje widok operacyjny (Notion). Człowiek zostaje przy decyzjach wysokiej wartości (HITL), nie przy przeklejaniu faktów.

---

## 3. Users and human boundary

| Użytkownik | Rola |
|------------|------|
| Adrian Domański | Primary user — faza testowa |
| Maciej Małysz | Docelowy primary user — Partner Inovo |

**Zasada:** system jest asystentem decyzyjnym, nie decydentem. Komunikacja outbound do founderów wymaga człowieka.

---

## 4. Success criterion

Partner w **≤ ~2 min** ma z rekordu deala zrozumieć: **kim jest spółka**, **czy mieści się w mandacie Inovo**, **skąd biorą się score’y**, **czego brakuje w materiale**, **jakie są główne ryzyka / mocne strony** oraz **jaki jest sensowny następny krok** — bez konieczności ponownego czytania całego PDF od zera.

### Product success metrics (MVP — mierzalne KPI)

North star powyżej zostaje; poniżej **progi operacyjne**, żeby „działa” nie było uznaniowe (wartości **X / Y / $** ustalasz z partnerem — tu placeholdery logiczne):

- **Kompletność rekordu:** ≥ **90%** przetworzonych leadów ma w `deals` albo komplet ścieżki AI (fakty + scorecard lub jawny stan pominięcia), albo **jawny** stan błędu (`ERROR` / `last_error_*`), nigdy „cichy sukces”.
- **Walidacja strukturalna:** ≥ **95%** odpowiedzi LLM kroków zapisujących do DB przechodzi walidację schematu (Pydantic); reszta → retry / błąd explicite (patrz [§ 23](#23-llm-output-contract)).
- **Użyteczność dla partnera (próbkowanie):** ≥ **80%** rekordów ocenionych jako „zrozumiałe bez ponownego czytania decku” w krótkim przeglądzie panelu (Notion / DB) na referencyjnym zestawie.
- **Gate 1:** wskaźnik false negative / false positive na **zestawie referencyjnym** poniżej uzgodnionego progu (patrz [§ 27](#27-evaluation-harness)).
- **Bezpieczeństwo outbound:** **0** automatycznie wysłanych maili do founderów (poza szkicem).
- **Odporność na Notion:** błąd sync Notion **nigdy** nie kasuje ani nie fałszuje rekordu w SQLite.
- **Czas i koszt (orientacyjnie):** mediana pełnego screeningu deck poniżej **X** min, www poniżej **Y** min; szacunkowy koszt LLM pełnego przebiegu poniżej **$Z** przy domyślnych limitach, o ile włączony nie jest kosztowny external / nie zmienisz modeli (szczegóły liczenia → telemetria w kodzie i `ARCHITECTURE.md`).

---

## 5. Expected result

Dla każdego leada system ma wyprodukować **jeden spójny rekord w `deals`** (oraz opcjonalnie widok w Notion):

1. **Tożsamość i źródło** — `company_name`, `message_id` / URL, `source_url` lub kontekst maila, znaczniki czasu.
2. **Mandat (Gate 1)** — `gate1_verdict`, `gate1_detected_*`, `gate1_rejection_reason` gdy odrzucone; mapowanie na **Innovo fit** w kodzie.
3. **Fakty z materiału** — `gate2_facts_json` (deck) lub odpowiednik po stronie www.
4. **Scorecard** — `gate2_dimensions_json`, score’y; `gate2_missing_critical_data`, `gate2_should_ask_founder`; `gate2_confidence` / flagi jakości tam gdzie ustawiane.
5. **Pakiet decyzyjny** — `innovo_fit_decision`, `deck_evidence_decision`, `generic_vc_interest`, oraz pole operacyjne **`final_action`** (reguły w `main.py`).
6. **Opcjonalnie zewnątrz** — `external_opportunity_score` po Gate 2.5.
7. **HITL** — status w bazie; możliwy szkic Gmail; brak auto-wysyłki.
8. **Notion** — odzwierciedlenie pól z DB + treść podstrony bez dodatkowego LLM na sync.

Szczegóły operacyjne layoutu → **`CURSOR_NOTION_INSTRUCTIONS.md`** (zasady dla agentów / zmian w sync).

---

## 6. Horizontal product flow

**Poziom 1 — end-to-end (produkt):** co dzieje się w dwóch ścieżkach wejścia i co jest wspólne.

| Etap | Deck / Gmail path | Website / URL path | Shared output |
|------|-------------------|--------------------|---------------|
| **0. Input** | Gmail message + optional PDF | Manual `assess-url` | Lead identity |
| **1. Initial context** | Email body + metadata | Website crawl / markdown | Source context |
| **2. Mandate** | Gate 1 on email + metadata | Gate 1 on website context / extracted facts | Mandate fit |
| **3. Deep material** | PDF → markdown if not hard fail | Website facts normalization | Structured source material |
| **4. Facts** | Gate 2A facts from deck | Facts aligned for scoring (post-normalization) | Structured facts |
| **5. Scoring** | Gate 2B/C scorecard + brief | Website score / VC pack | VC assessment |
| **6. Decision** | Python rules in `main.py` | Python rules in `run_assess_url` | `final_action` + business interpretation |
| **7. Enrichment** | Optional Gate 2.5 | Optional Gate 2.5 | External opportunity signal |
| **8. HITL** | Terminal / Gmail draft | Terminal / Notion | Human review |
| **9. Operating view** | Notion sync from SQLite | Notion sync from SQLite | Same deal record view |

**Poziom 2 — implementacja:** szczegółowa tabela faz, modułów i kryteriów „done” → [§ 13. Pipeline implementation phases](#13-pipeline-implementation-phases).

---

## 7. Primary user journeys

### Journey A — Email + pitch deck

1. Przychodzi inbound mail; system (poll Gmail) wczytuje wiadomość (i wie, czy jest załącznik PDF — **bez** treści wyekstrahowanego decku w tym momencie).
2. **Gate 1 — szybki filtr mandatu** na podstawie **treści maila i metadanych** (`gate1_fit_check`); prompt wie tylko, że PDF jest/nie ma (`attachment_info`), **nie czyta markdownu decku**.
3. Przy twardym **FAIL_CONFIDENT** (bez debug override) pipeline może zakończyć się tutaj — deck nie jest analizowany LLM Gate 2.
4. Gdy ścieżka idzie dalej (**PASS / UNCERTAIN_READ_DECK** lub override): pobranie PDF (jeśli jest) → **PDF → tekst lokalnie**; przy bezużytecznym tekście → zapis błędu / review.
5. **Gate 2** — `run_gate2_pipeline`: ekstrakcja faktów z decku → scorecard (+ brief wg trybu) → zapis rekordu w SQLite.
6. Opcjonalnie enriched / Gate 2.5 zgodnie z regułami i env.
7. Opcjonalnie szkic Gmail (approve/reject), **bez auto-wysyłki**.
8. Opcjonalnie sync do Notion jako tablica operacyjna.

### Journey B — Website / URL (`assess-url`)

1. Użytkownik uruchamia screening na URL.
2. System zbiera treść strony → fakty → Gate 1 www → scoring / VC pack.
3. Ten sam **logiczny** rekord deala ląduje w `deals` co przy decku.
4. Opcjonalnie Notion — ten sam układ widoku.

### Journey C — Partner review

1. Partner otwiera **Notion** i/lub wynik w **terminalu**.
2. Czyta: tożsamość, mandat, score’y, braki (`missing`), mocne strony / ryzyka, **interpretację biznesową** (polami typu Innovo fit / rekomendacja), następny krok.
3. Decyzja człowieka: odrzucić, poprosić o więcej info, rozpatrzyć osobiście, eskalować — **poza automatycznym pipeline’em**.

---

## 8. Inovo mandate reference

Ramki **stage / ticket / geo / sector** funduszu — kontekst dla Gate 1 i oceny fitu; **to nie jest journey**, tylko referencja produktowa mandatu.

- **Stage:** Pre-Seed, Seed (rzadko Series A)
- **Ticket:** €100k–€10m (initial €0.5m–€4m)
- **Geografie:** CEE + diaspora (PL, LT, HR, RS, UA, LV, EE, RO, BG, SI, CZ, HU, SK, …). **HQ poza CEE (np. SF po YC) nie wyklucza mandatu**, jeśli **founderzy mają korzenie CEE / diaspora** — to jest pełnoprawny fit Inovo. Strona często podaje tylko „US HQ”; produktowo dopuszczalny jest **kontrolowany krok OSINT** (neutralne zapytania + dopasowanie do leksykonu CEE z `config/criteria.py`, bez zakładania konkretnego kraju), z wynikiem w `inferred_signals`, zanim Gate 1 zrobi twardy `FAIL` wyłącznie po treści strony.
- **Sektory:** Developer Tools, AI/ML, Healthcare, SaaS, Marketplaces, B2B/B2C Enterprise

---

## 9. Partner Brief Contract

Rekord prezentowany partnerowi (Notion + pola w DB) powinien pozwolić spełnić kryterium **≤ ~2 min**. Minimalnie — logicznie, nawet jeśli część jest uzupełniana w kolejnych polach UI:

1. **One-liner / kim jest spółka** — produkt lub firma w jednym zdaniu.
2. **Mandate fit** — geo / stage / sector (z Gate 1 + faktów).
3. **Interpretacja biznesowa** — Innovo fit, deck evidence, generic VC (lub odpowiedniki www), werdykt / rekomendacja Gate 2 tam gdzie dotyczy.
4. **Top mocne strony** — lista (np. do 3–5), powiązana ze scorecardem / VC packiem.
5. **Top ryzyka / concerns** — lista (np. do 3–5).
6. **Missing critical data** — jawna lista luk.
7. **Suggested next step** — rekomendowany następny krok (tekst lub pole wyprowadzone z pipeline’u).
8. **Confidence** — przynajmniej poziom pewności scorecardu (`gate2_confidence` lub ekwiwalent przy www), tam gdzie dostępny.
9. **Źródła i linki** — mail (np. Gmail link), deck nie jest „linkowany” jako URL ale jest ścieżką przetwarzania; **URL strony** przy ścieżce www; przy Gate 2.5 — że external był użyty i że snippetty mają źródła w module.

Sekcje strony rekordu w Notion → [§ 10. Notion operating view](#10-notion-operating-view).

---

## 10. Notion operating view

**Poziom tabeli (widok dla operatorów)** — w widoku listy/database **dokładnie 5 widocznych kolumn**: `Company` (Title), `Score`, `Status`, `Sector`, `Received At`. Pozostałe pola w schemacie Notion są **hidden** (nadal zapisywane przez sync). Nie dodawać kolejnych widocznych kolumn bez świadomej zmiany procesu — patrz `CURSOR_NOTION_INSTRUCTIONS.md`.

**Poziom strony rekordu (treść deala)** — „subpage” oznacza tu **treść na stronie rekordu w bazie Notion**, a **nie** osobny podstronowy dokument ani osobna strona-dziecko w hierarchii Notion.  
Format raportu jest **VC-first (investment memo)**: najpierw ocena tezy Inovo, potem opis spółki i szczegóły.

**Struktura techniczna (API)** — dokładnie **10 par bloków** = **20 bloków** z rzędu: dla każdej sekcji najpierw **`heading_2`**, potem **`paragraph`** z treścią sekcji. Budowa listy: `_build_deal_summary_blocks`; zapis na stronę: `_ensure_page_summary_blocks` — przy poprawnej kolejności i braku duplikatów **aktualizacja treści przez `PATCH` akapitu** pod danym nagłówkiem; przy złej kolejności lub duplikatach — **odbudowa** przez dopisanie nowych `children` po zarchiwizowaniu starych zarządzanych bloków. Sekcja **🔍 Raw Notes** jest wyjątkiem: domyślny placeholder to `— brak notatek —`; **treści wpisanej przez partnera sync nie nadpisuje** (tylko zachowuje przy przebudowie struktury).

**Kolejność i zawartość sekcji** (`_build_deal_summary_blocks` i terminal memo):

| # | Nagłówek | Zawartość |
|---|----------|-----------|
| 1 | 0. Inovo Decision | Jedna sekcja decyzyjna: `InovoMandateFit.overall`, `InvestmentInterest.overall`, finalny werdykt, następny krok, one-line reason. |
| 2 | 1. Inovo Fit Check | Deterministyczne osie tezy: geo/CEE link, stage, sector, ticket, software layer; każde z `PASS/UNCERTAIN/FAIL`. |
| 3 | 2. Company Snapshot | Fakty operacyjne: nazwa, URL, founderzy, HQ/legal, CEE sygnały, stage, sektor, model. |
| 4 | 3. Why It Could Be Interesting for Inovo | Pozytywne sygnały inwestycyjne, bez tabel i bez score-first framing. |
| 5 | 4. Risks and Missing Data | Ryzyka + luki decyzyjne w jednym miejscu. |
| 6 | 5. Open Questions | Konkretne pytania na deck/call. |
| 7 | 6. Evidence Log | Źródła i ścieżka dowodowa (crawl, ekstrakcja, resolvery, external). |
| 8 | 🔍 Raw Notes | Pole partnera; sync go nie nadpisuje. |

### Ważne: sync Notion **nie woła LLM**

Treść 9 sekcji jest **zbudowana deterministycznie z wiersza `deals`** (parsowanie JSON z kolumn + pola decyzyjne). Żaden prompt „do Notiona” nie istnieje — **render** jest czystym formatowaniem już zapisanego stanu pipeline’u (`agents/notion_sync.py`). Źródła pytań do modelu są **wcześniej**: Gate 1 / Gate 2 / www / Gate 2.5 — prompty w `config/prompts.py`, `config/prompts/external_*.md`, oraz ścieżka www w modułach website.

### Które „pytania idą do LLMa” — mapowanie na produkt (wysoki poziom)

**Ścieżka deck (mail)**

| Krok LLM | Skrót roli promptu | Gdzie w kodzie | Klucze promptów (`config/prompts.py`) |
|---------|---------------------|----------------|---------------------------------------|
| Gate 1 | Czy inbound mieści się w mandacie funduszu (bez treści PDF) | `agents/screener.py` → `gate1_fit_check` | `GATE1_SYSTEM`, `GATE1_USER` |
| Gate 2A | Ekstrakcja **faktów** z markdownu decku (bez scoringu) | `agents/screener.py` → `run_gate2_pipeline` cz. A | `GATE2A_SYSTEM`, `GATE2A_USER` |
| Gate 2B | **Scorecard** wymiarów 1–10 na podstawie wyłącznie JSON faktów | cz. B | `GATE2B_SYSTEM`, `GATE2B_USER` |
| Gate 2C | **Brief** pod partnera na podstawie faktów + wyników scorecardu | cz. C | `GATE2C_SYSTEM`, `GATE2C_USER` |
| Gate 2.5 | Plan researchu → snippetty źródeł → ocena rynku zewnętrznego | `agents/external_check.py` (+ prompty `.md`) | `config/prompts/external_research_queries.md`, `external_market_check.md` |

**Ścieżka WWW (`assess-url`)**

| Krok LLM | Skrót roli | Gdzie w kodzie |
|---------|-------------|----------------|
| Ekstrakcja faktów ze strony | Markdown crawl → strukturalne fakty | `agents/website_screener.py` (telemetria VC LLM przez `agents/website_vc_llm.py` tam gdzie dotyczy) |
| Gate 1 www | Mandat na ekstrahowanych faktach / kontekście www | ten sam moduł |
| Pakiet VC www | Jedna lub batchedowa warstwa „VC pack” (12 wymiarów / narracja www) | `agents/website_screener.py`, limity wyjścia w `config/llm_cost.py` (`TOK_WEBSITE_*`) |
| Gate 2.5 | Jak przy decku, tryb `screening_mode="website"` | `agents/external_check.py` |

### Macierz: 9 sekcji Notion → skąd treść → które LLM ją „nakarmiły”

Wszystkie sekcje poniżej są **projekcją z SQLite** (kolumny w `storage/database.py`, zapis przez `save_gate1`, `save_gate2`, `save_website_assessment_details`, `save_screening_decisions`, `save_gate25`).

| Sekcja | Główne pola / JSON w `deals` | Deck: etapy LLM, które to zasilają | WWW: etapy LLM |
|--------|------------------------------|--------------------------------------|----------------|
| **⚡ Decision Snapshot** | `deck_evidence_score` / `gate2_overall_score`, `external_opportunity_score`, `innovo_fit_*`, `final_action`, `gate1_verdict`, `gate2_recommendation`, fragmenty Gate 1 (geo/stage), heurystyki „why blocked” | Gate 1 + Gate 2B/C (score, rekomendacja, rationale) + opcj. Gate 2.5 (tylko **skalar** external w snapshot; pełny JSON Gate 2.5 jest w DB — patrz uwaga niżej) | Www VC + blend + external → te same pola decyzyjne po zapisie |
| **📋 Deal Summary** | `gate2_facts_json` + meta (`company_one_liner`, nadawca, daty, link Gmail / `source_url`) | **Gate 2A** (fakty), Gate 1 (sector/geo jeśli w wierszu) | Ekstrakcja faktów ze strony |
| **🎯 Screening Result** | `gate2_summary`, `innovo_fit_decision`, `deck_evidence_decision`, `generic_vc_interest`, `final_action`, `gate2_recommendation`, rationale; skrót wymiarów z `gate2_dimensions_json` | Gate 2C (brief), Gate 2B (wymiary), decyzje policzone w `main.py` | VC pack www + decyzje w `run_assess_url` |
| **💪 Strengths** | `gate2_strengths` lub pola w `gate2_dimensions_json` (`top_strengths`, scorecard) | Gate 2B/C | VC pack www |
| **⚠️ Risks** | `gate2_concerns` / `top_risks` / wymiary niskie | Gate 2B/C | VC pack www |
| **🏁 Market Context** | Fragmenty narracji z `gate2_dimensions_json` (np. `why_not_higher`, sygnały saturation/timing), opcjonalnie pola pomocnicze z bloba www | Gate 2B/C | VC layer www + heurystyki w kodzie renderu |
| **❓ Missing & Follow-ups** | `gate2_missing_critical_data`, `gate2_quality_flags`, `gate2_should_ask_founder` (+ listy z bloba www w JSON) | Gate 2B/C | VC pack www |
| **➡️ Recommended Next Step** | `recommended_next_step` z JSON / `gate2_summary` | Gate 2C (+ składanie w kodzie) | WWW assessment |
| **🔍 Raw Notes** | Partner — **nie generowane przez pipeline** | — | — |

**Uwaga (transparentność Gate 2.5):** szczegółowy output external (źródła, dim-y zewnętrzne) trafia do **`gate25_external_json` / `gate25_final_decision_json`** w SQLite po `save_gate25`. W **ciało strony Notion** na dziś wchodzi przede wszystkim **`external_opportunity_score`** jako liczba w Decision Snapshot oraz property „External Opportunity Score”; pełny raport JSON nie jest powielany 1:1 w akapicie — jest w bazie i w module external. Jeśli produktowo ma być widoczny Pełny JSON w Notion, to osobna zmiana renderu w `notion_sync.py`.

**Uwaga (confidence):** `gate2_confidence` jest utrzymywane w schemacie dla ścieżki deck i — po zapisie www — dla przejrzystości w DB; **tekst 9 sekcji na stronie rekordu nie ma dziś osobnej linii „Confidence”** (można dodać w kolejnej iteracji renderu).

### Terminal vs Notion — zbieżność

- **Ten sam rekord logiczny:** terminal pokazuje wynik przebiegu (`main.py`: `process_email`, `run_assess_url`) zanim lub równolegle z zapisem; Notion dostaje **ten sam stan po zapisie do `pipeline.db`** (`sync_one_deal_to_notion`).
- **Ten sam rdzeń treści:** sekcje strony rekordu odpowiadają temu, co zostało policzone i zapisane (snapshot, fakty, scorecard w JSON, strengths/risks, missing).
- **Różnice kosmetyczne:** terminal ma bogatszy „live” log (token report na końcu runu); Notion ma **ściślejszy layout** paragrafów i może **uciąć** długie pola (`_clip` w kodzie). Priorytet „co widać w całości”: **SQLite** i logi jobów.
- **Telemetria kosztów:** zużycie tokenów / szacunkowy koszt są zapisywane przy etapach w `deals` i drukowane przez `_print_token_report` w `main.py` po przebiegu — to jest referencja runtime; Notion nie pokazuje kosztów.

### Limity kosztowe i tokenów (kontrakt produktowy)

System **musi** pozwalać na konfigurowalne sufity kosztowe per etap LLM (żeby jeden pipeline nie „zjadał” budżetu). Bieżące wartości domyślne, nazwy zmiennych env i tablica sufitów są utrzymywane w **`config/llm_cost.py`** i opisane w **`ARCHITECTURE.md`** — nie duplikujemy ich tu, żeby PRD nie rozjeżdżał się przy zmianie domyślnych liczb.

**Zasady produktowe:** każdy etap LLM ma mieć znany **model** (ciężki vs lekki), **limit wyjścia** i zapisywaną **telemetrię** tokenów tam gdzie wdrożone; raport kosztowy ma być widoczny w przebiegu (terminal / DB).

Przy aktualizacji istniejącego rekordu sync **nie nadpisuje treści 🔍 Raw Notes**, jeśli partner już coś dopisał (logika w `_ensure_page_summary_blocks`). Upsert identyfikuje rekord po **`message_id`** tam gdzie dostępne.

Szczegółowe formaty pól per sekcja (operacyjne dla edycji sync) są też w **`CURSOR_NOTION_INSTRUCTIONS.md`**. **Intencja produktowa** jest zgodna z **`agents/notion_sync.py`**; szczegóły renderu i kolejność bloków — **implementacja** w `notion_sync.py` ([reguła canonical § 1](#1-purpose-of-this-document)).

---

## 11. Agent model and source of truth

**Przepływ kontekstu:** zbiór kontekstu → mandat → fakty → scorecard → decyzje w kodzie → SQLite → (opcj.) external → człowiek → (opcj.) Notion.

Output LLM jest wejściem do **walidacji i zapisu**, nie źródłem prawdy dopóki nie trafi do `deals`.

| Warstwa | Rola |
|---------|------|
| **SQLite (`pipeline.db`)** | Operacyjne źródło prawdy dla deali i statusów pipeline’u. |
| **Notion** | Widok workflow; pola zsynchronizowane z bazy. |
| **Wyjścia LLM** | Po parsowaniu i zapisie w kolumnach; surowy tekst ≠ rekord. |

Szczegółowe mapowanie plików → [Appendix A](#appendix-a-canonical-source-of-truth-index).

---

## 12. Decision model (single-fund deterministic)

W kodzie decyzja jest liczona deterministycznie z nowego modelu Inovo: `InovoMandateFit` + `InvestmentInterest` + blokery.

| Pojęcie | Znaczenie | Przykłady |
|---------|-----------|-----------|
| **Mandate fit** | Oś tezy Inovo (geo/stage/sector/ticket/software). | `PASS`, `UNCERTAIN`, `FAIL` |
| **Investment interest** | Atrakcyjność inwestycyjna z sygnałów produktu, teamu, rynku, trakcji. | `HIGH`, `MEDIUM_HIGH`, `MEDIUM`, `LOW` |
| **Final verdict** | Deterministyczny wynik mapowania fit+interest+blockers. | `REVIEW_DECK_OR_TAKE_CALL`, `REQUEST_DECK`, `REQUEST_DECK_AND_VERIFY`, `REJECT_OR_ARCHIVE` |
| **final_action** | Warstwa operacyjna workflow (kompatybilność pipeline). | `PASS_TO_PARTNER`, `ASK_FOR_MORE_INFO`, `STOP` |

**Reguła twarda:** brak potwierdzonego sygnału CEE nie może wygenerować końcowego `PASS` w osi mandate; maksymalnie `UNCERTAIN` do czasu wzbogacenia/resolverów.

Szczegółowe wartości `final_action` dziś m.in.: `STOP`, `RUN_ENRICHED_SCREEN`, `PASS_TO_PARTNER`, `ASK_FOR_MORE_INFO`, `TEST_CASE_ONLY` — patrz `main.py` i ścieżka website.

---

## 13. Pipeline implementation phases

**Ścieżka deck (mail) — kolejność jak w `process_email`:** najpierw **Gate 1 na samym mailu** (mandat; bez treści wyekstrahowanego PDF), potem — jeśli pipeline nie kończy się na twardym failu — **pobranie PDF → markdown**, następnie **Gate 2** (ekstrakcja z decku, scoring, brief wg trybu).

**Ścieżka WWW:** ingest treści strony → ekstrakcja faktów → **Gate 1 www** na faktach → scoring / VC pack (kolejność i nazwy kroków w module website).

**Fazy 5+** (decyzje złożone, zapis, external, HITL, Notion) **zbiegają się** przy tabeli **`deals`**.

**Bramki:** **Gate 1** = mandat (email-only przy deck path); **Gate 2** = głęboka ocena materiału deckowego lub www; **HITL** = człowiek. Prompty: `config/prompts.py` + website screener.

| Faza | Wejście | Wyjście | Domknięcie gdy… | Moduły (skrót) |
|------|---------|---------|-----------------|----------------|
| **0** | Mail / URL / CLI | identyfikacja leada | jest `message_id` lub ekwiwalent | `main.py`, `gmail_client` / `run_assess_url` |
| **1a (deck)** | Treść maila + meta załącznika | Gate 1 | zapisane `gate1_*` | `gate1_fit_check` — **tylko email** |
| **1b (www)** | Zebrana treść strony | markdown / kontekst | gotowe do LLM | warstwa website |
| **2 (deck)** | Decyzja ≠ twardy stop | PDF → markdown | tekst OK **lub** zapisany błąd | pymupdf4llm |
| **2 (www)** | Strona | fakty | po `extract_facts` | `WebsiteScreeningAgent` |
| **3 (www)** | Fakty | Gate 1 www | zapis mandatu www | `WebsiteScreeningAgent.gate1` |
| **4 (deck)** | Markdown decku | JSON faktów (Gate 2A) | `gate2_facts_json` | `run_gate2_pipeline` cz. A |
| **5 (deck)** | Fakty z decku | Scorecard + brief (Gate 2B/C) | `gate2_dimensions_json`, score’y | `run_gate2_pipeline` cz. B/C |
| **6** | Wyniki Gate 2 **lub** ukończony pakiet www | Decyzje złożone + zapis w `deals` | `save_*` wykonane | `main.py`, `database.py` (+ ścieżka www) |
| **7** | Rekord | external (opcj.) | external zapisany lub pominięty świadomie | `run_gate25_external_check` |
| **8** | Rekord | HITL | partner / draft | `main.py`, `orchestrator` |
| **9** | Wiersz SQLite | Notion | sync OK lub jawny błąd | `notion_sync.py` |

*WWW (faza 6):* między Gate 1 www a zapisem — scoring / VC pack / merge — kanonicznie `main.run_assess_url`, `agents/website_screener.py`, pomocniczo `agents/website_*.py`.

Schemat: **Deck** i **WWW** → **SQLite** → opcj. **Gate 2.5** → **Notion**.

---

## 14. Scoring and confidence

**Pytania analityczne per wymiar (evidence, capy, why_not_higher, …):** [§ 24](#24-llm-screening-question-map) + szczegóły w **[LLM_SCREENING_SPEC.md](LLM_SCREENING_SPEC.md)** (wspólnie z `SCREENING_RUBRIC.md`).

### Deck — wymiary VC

**Kontrakt logiczny:** score 1–10, uzasadnienie, evidence / missing / why_not_higher wg **`agents/schemas.py`** (`Gate2ScoreOutput`, `DimensionScore`). Lista wymiarów i wagi → **`SCREENING_RUBRIC.md`**, `config/scoring.py`, `GATE2_PASS_THRESHOLD`.

### Website (`assess-url`)

Materiał wejściowy to **strona WWW**, nie deck — score i narracja muszą **jawnie** uwzględniać ograniczenia źródła.

Pełny kontrakt www (klasyfikacja twierdzeń, capy) → [§ 30](#30-website-screening-contract).

- Rozróżnienie w treści / polach tam gdzie możliwe: **fakty widoczne na stronie** vs **sygnały wywnioskowane** vs **brak informacji**.
- **Missing / słabe twierdzenia** — jawne; website-only nie zastępuje decku pod kątem traction, rundy, cap table, szczegółów finansowych.
- Score **nie powinien udawać** pełnego „deck score”, jeśli brakuje kluczowych danych — wtedy niski confidence, długa lista missing lub explicite „potrzebny deck / call”.
- Pole lub opis powinien pozwalać odpowiedzieć partnerowi: **czy sama strona wystarcza do merytorycznego review**, czy **tylko do wstępnego triage**.

Szczegóły implementacji → pipeline website (`agents/website_screener.py`, `WebsiteScreeningAgent`, VC pack strony).

### Pewność, braki, jakość materiału

- **`gate2_confidence`** — pewność scorecardu.  
- **`missing_critical_data`**, **`should_ask_founder`** — jawne luki.  
- **`gate2_quality_flags`** (i pokrewne) — jakość po walidacji.

Cel: unikać **fałszywej precyzji**; niski confidence lub długa lista missing → **review**, nie „pewna prawda”.

---

## 15. Gate 2.5 — external research

**Zasady**

- **Uzupełnia**, nie zastępuje decku/strony; kontrola przez `run_gate25_external_check` i prompty `config/prompts/external_*.md`.
- **Tavily** opcjonalnie (`TAVILY_API_KEY`, `EXTERNAL_WEB_SEARCH`); twierdzenia z webu — ze **źródłem** w wyniku modułu.
- Bez web search możliwy tryb **LLM-only**: model operuje wyłącznie na **dostarczonych faktach i kontekście z pipeline’u**, bez **świeżej walidacji w internecie** (to nie jest „ukryty web research”). Patrz `agents/external_research.py`, `EXTERNAL_WEB_SEARCH`.

**Checklist intencji (co external ma wspierać)**

1. **Istnienie i świeżość firmy** — działająca strona; publiczna tożsamość firmy / founderów tam gdzie dostępne; ślady aktywności.
2. **Zespół / founderzy** — spójność z materiałem; sygnał CEE / diaspora jeśli dotyczy mandatu (bez udawania pełnego KYC).
3. **Kontekst rynku** — dynamika kategorii, pilność problemu, aktywność fundingowa, szersze trendy regulacyjne / technologiczne (wg dostępnych snippetów).
4. **Konkurencja** — gracze bezpośredni, substytuty, nasycenie, sygnały różnicowania (ograniczone przez jakość źródeł).
5. **Red flags** — niespójna tożsamość firmy/founderów, martwa / generyczna strona, podejrzane twierdzenia o traction, brak jakiejkolwiek aktywności.

Szczegółowe pola wyjścia external → **`agents/schemas_gate25.py`** oraz zapis w `deals` przez `agents/external_check.py`.

Polityka źródeł i konfliktów → [§ 31](#31-external-source-policy).

---

## 16. Escalation, failure handling, Definition of Done

### Eskalacja i awarie

- **Biznesowo:** niski confidence, granica progu, konflikt sygnałów → **review / ASK_FOR_MORE_INFO**, nie „ciche odrzucenie”.
- **Technicznie:** `save_error`, statusy w `deals`, etykiety Gmail tam gdzie dotyczy.
- PDF nieczytelny, błąd crawl strony / niska jakość tekstu, timeout API, invalid JSON (retry), brak Tavily, błąd Notion → **widoczny stan**, nie udawanie udanego screeningu.

### Definition of Done (jeden przebieg)

- Zapisany wynik **Gate 1** lub jawne wcześniejsze zakończenie (błąd wejścia).
- Dla pełnej ścieżki AI: zapisane **fakty** i **scorecard** (lub jawny skip z przyczyną).
- **`final_action` i pola decyzyjne** policzone i w SQLite; **timestampy** (`created_at` / `updated_at`) odzwierciedlają przebieg.
- Żadna wiadomość do foundera **nie wyszła automatycznie** poza szkicem.
- Notion opcjonalny; błąd sync **nie** kasuje prawdy w SQLite.
- Przy błędzie: **zapisany** stan błędu / status, nie „pusty sukces”.
- **Audytowalność przebiegu:** każdy screening ma mieć stabilny **`run_id`** (lub równoważny identyfikator batcha) powiązany z wejściem, modelami, telemetrią i wynikiem — patrz [§ 28](#28-run-auditability). *Implementacja może być minimalna na start (np. UUID + log), ale wymóg produktowy jest już teraz.*

---

## 17. Evaluation and audit

Wymóg szczegółowy i zestaw referencyjny → [§ 27. Evaluation harness](#27-evaluation-harness). Oczekiwane odpowiedzi modeli muszą być zgodne z mapą pytań → [§ 24](#24-llm-screening-question-map) / **`LLM_SCREENING_SPEC.md`**.

Krótko: eval jest **częścią produktu** dla agenta LLM — nie „po premierze”. Zmiana promptu / rubryki / modelu bez kontroli regresji na zestawie referencyjnym jest **świadomą decyzją**, nie przypadkiem.

---

## 18. Deduplication

Ten sam deal może pojawić się wielokrotnie (różne maile, intro, URL vs deck, ponowny mail).

**Zachowanie docelowe:** unikać **równoległych** rekordów dla tego samego podmiotu tam gdzie to możliwe — sygnały: `message_id`, znormalizowana nazwa firmy, domena, nadawca, kanoniczny URL, istniejący wpis w SQLite / Notion. Przy wykryciu duplikatu: **aktualizacja lub powiązanie** z istniejącym rekordem zamiast cichego drugiego wpisu.

*Implementacja może być częściowa — ten akapit jest **wymaganiem produktowym** do domknięcia w kolejnych iteracjach.*

---

## 19. Security and privacy

- Materiały founderów (deck, treść strony, maile) mogą być **poufne** — traktować jako dane wewnętrzne funduszu.
- **Brak automatycznej wysyłki** maili do founderów — tylko szkice / ręczna wysyłka.
- **Klucze API** wyłącznie w zmiennych środowiskowych / `.env`; **nie commitować** `.env` ani sekretów.
- **OAuth Gmail** — `credentials.json` / `token.json` lokalnie; wykluczyć z gita (`.gitignore`).
- **Notion** — pokazywać tylko pola przeznaczone na workflow wewnętrzny; nie udostępniać integracji publicznie bez kontroli.
- **External research** — nie wysyłać do narzędzi zewnętrznych **całego** decku, jeśli nie jest to potrzebne; stosować się do tego, co faktycznie przekazuje `external_check` (fakty + kontekst zaplanowanych zapytań).
- **Logi** — unikać domyślnego zrzutu pełnego decku w logach produkcyjnych; tryb debug lokalnie.

Polityka retencji i dostępu → [§ 32](#32-data-retention-and-access-control).

---

## 20. Non-goals

- Pełna weryfikacja księgowa liczb z decku.
- Automatyczna korespondencja bez człowieka.
- Gwarancja złapania każdego „dobrego” deala (cel: **nie gubić obiecujących** przez review zamiast ślepego reject).

---

## 21. Integrations and stack

Poniżej integracje używane przez produkt, obowiązkowość oraz **canonical implementation**.

| Usługa / warstwa | Rola w produkcie | Wymagana? | Canonical implementation |
|------------------|------------------|-----------|--------------------------|
| **OpenAI API** | LLM (Gate 1/2, www, external) | Tak dla AI | `OPENAI_API_KEY`, `config/llm_cost.py` |
| **Gmail** | Wejście inbound, PDF, etykiety, **szkice** (bez auto-wysyłki) | Tak dla ścieżki mail; `--test` / sam `assess-url` mogą bez | `tools/gmail_client.py`, `GMAIL_*`, OAuth |
| **SQLite** | Operacyjne źródło prawdy dla deali | Tak lokalnie | `storage/database.py`, `pipeline.db` |
| **Notion** | Widok operacyjny + strona rekordu (sync z DB, bez LLM na sync) | Nie | `NOTION_*`, `agents/notion_sync.py` |
| **Tavily** | Snippety www przy Gate 2.5, gdy włączone | Nie | `TAVILY_API_KEY`, `EXTERNAL_WEB_SEARCH`, `agents/external_research.py` |
| **PDF** | PDF → markdown lokalnie | Tak na ścieżce deck | `pymupdf4llm` |
| **LinkedIn** | Tylko URL wyszukiwania w konsoli | Nie | `main.py` |
| **Calendly** | Placeholder w szablonie maila, nie API | Nie | `config/prompts.py` |
| **Terminal / Rich** | HITL, podgląd | Środowisko lokalne | CLI w `main.py` |

---

## 22. Acceptance criteria

Poniżej kryteria akceptacji **behawioralne** (Given / When / Then). Szczegóły techniczne (np. `MAX_PDF_MB`) → `ARCHITECTURE.md` i env.

### Email + PDF (ścieżka Gmail)

**Given** wiadomość Gmail z załącznikiem PDF mieszczącym się w limitach wejścia,  
**when** pipeline jest uruchomiony i Gate 1 **nie** zwraca `FAIL_CONFIDENT` (w trybie normalnym),  
**then** system **musi** pobrać PDF, zdekretować do markdownu lokalnie, uruchomić Gate 2A → 2B → 2C, zapisać wyniki w `deals`, policzyć pola decyzyjne i `final_action`, opcjonalnie utworzyć **szkic** Gmail — **bez** automatycznej wysyłki.

**Given** Gate 1 zwraca `FAIL_CONFIDENT` (poza mandatem),  
**when** brak debug override,  
**then** pipeline **może** zakończyć się przed pełnym Gate 2; **musi** zachować powód odrzucenia i spójny status / pola Gate 1.

### Website (`assess-url`)

**Given** poprawny URL,  
**when** uruchomiono `assess-url`,  
**then** system **musi** zebrać treść strony, wyciągnąć fakty, wykonać Gate 1 www i scoring www, zapisać rekord w `deals`, oznaczyć braki deck-level explicite zgodnie z [§ 30](#30-website-screening-contract).

### Notion sync

**Given** poprawny rekord w SQLite,  
**when** sync Notion się nie powiedzie,  
**then** SQLite pozostaje źródłem prawdy; błąd jest widoczny (log / konsola / retry manualny), rekord **nie** jest usuwany ani „zerowany”.

### Walidacja wyjść LLM

**Given** krok LLM zapisuje do bazy,  
**when** odpowiedź nie przechodzi walidacji schematu po retry,  
**then** system **musi** zapisać jawny stan błędu, nie udawać udanego screeningu ([§ 23](#23-llm-output-contract)).

---

## 23. LLM output contract

Wszystkie kroki LLM, które zasilają `deals`, muszą spełniać:

1. **Structured output** — odpowiedź jest parsowana do modeli Pydantic / JSON Schema (`agents/schemas.py`, `agents/schemas_gate25.py`, modele Gate 1/2 w `storage/models.py` tam gdzie dotyczy).
2. **Walidacja przed zaufaniem** — surowy tekst modelu **nie** jest traktowany jako prawda dopóki nie przejdzie walidacji i nie zostanie zmapowany na kolumny.
3. **Retry** — przy invalid JSON / validation error: co najmniej jedna próba naprawy (retry / ponowne wywołanie) zgodnie z implementacją w module; po wyczerpaniu limitu → **jawny** błąd w pipeline.
4. **Brak zmyślania faktów** — brak danych w materiale = `missing`, `unknown`, lub adekwatne pole puste; nie uzupełniaj traction/revenue „na czuja”.
5. **Transparentność external** — twierdzenia z Gate 2.5 muszą być powiązane ze **źródłami** w wyniku modułu (`agents/external_check.py`, `agents/external_research.py`).

*Uwaga OpenAI Structured Outputs:* repo może ewoluować w stronę twardszego JSON Schema w API; wymóg produktowy pozostaje: **walidowalny output**, nie format ad hoc.

---

## 24. LLM screening question map

Ten paragraf to **warstwa analityczna**: *jakie klasy pytań* LLM musi rozstrzygnąć na każdym etapie. Nie zastępuje promptów ani rubryki — je **spiął** dla agentów / kodu.

**Pełna rozpiska (drzewo pytań, Gate 1 / 2A / 2B / 2C / 2.5 / www, reguły missing, overall z kodu):** → **[LLM_SCREENING_SPEC.md](LLM_SCREENING_SPEC.md)**

### Skrót per gate

| Gate | Wejście (zgrubnie) | Co LLM musi „wyciągnąć” (intencja) |
|------|--------------------|-------------------------------------|
| **Gate 1** | Treść maila (+ meta PDF, bez decku) | Mandat Inovo: geo / stage / sector / „czy pitch”, `FAIL_CONFIDENT` tylko przy dowodzie w mailu; przy niepewności + PDF → `UNCERTAIN_READ_DECK` |
| **Gate 2A** | Markdown decku (+ kontekst maila w polu trusted) | **Tylko fakty** — tożsamość, problem, produkt, klient, rynek, traction z liczbami/cytatami, zespół, fundraising, konkurencja, **missing** — **bez** score’ów VC |
| **Gate 2B** | JSON faktów | Dla każdego z **11 wymiarów** (`agents/schemas.py`): score, evidence, missing, why_not_higher/lower, confidence — zgodnie z **`SCREENING_RUBRIC.md`** |
| **Gate 2C** | Fakty + wymiary + overall z **kodu** | Brief partnerski: **bez nowych faktów** — tylko synteza 2A/2B |
| **Gate 2.5** | Fakty + kontekst | Plan zapytań → źródła → ocena zewnętrzna; konflikty jawne ([§ 31](#31-external-source-policy)) |
| **WWW** | Crawl strony | Ta sama **logika gatunkowa** co wyżej, z klasyfikacją *stated vs inferred vs missing* ([§ 30](#30-website-screening-contract)) |

**Overall score (deck):** **`config/scoring.py`** liczy średnią ważoną z wymiarów — LLM dostaje ją jako kontekst do Gate 2C, nie „wymyśla” finalnej liczby z głowy.

---

## 25. Minimum screening rubric contract

`SCREENING_RUBRIC.md` jest **obowiązkowym** dokładnym opisem rubryki dla scorecardu deck (11 wymiarów). Dla każdego wymiaru musi być możliwe uzasadnienie:

- **nazwa i definicja** — co mierzy;
- **skala 1–10** — sens punktacji;
- **kotwice** — co znaczy wynik niski vs wysoki (co najmniej pały dla review);
- **wymagany evidence** — jak cytować fakty z JSON faktów;
- **missing behavior** — co robić przy braku sygnału;
- **caps / flagi** — kiedy obniżyć score lub oznaczyć ryzyko;
- **`why_not_higher` / `why_not_lower`** — wymóg wyjaśnialności (zgodnie ze schematem Gate 2);
- **confidence** — kiedy obniżyć `dimension_confidence` / `gate2_confidence`.

Zmiana rubryki bez bumpu wersji (patrz [§ 26](#26-prompt-rubric-and-model-versioning)) jest **zmianą produktową** wymagającą świadomej decyzji.

---

## 26. Prompt, rubric, and model versioning

Każdy **zapisany wynik screeningowy** powinien umożliwiać odtworzenie kontekstu scoringu:

- **wersja / hash** rubryki (`SCREENING_RUBRIC.md`) i/lub `config/scoring.py`;
- **wersja / hash** zestawu promptów (`config/prompts.py`, `config/prompts/external_*.md`);
- **nazwa modelu** i provider (`OPENAI_MODEL`, `OPENAI_MODEL_LIGHT`, ewentualne override’y per krok);
- **tryb external** (Tavily vs LLM-only: `EXTERNAL_WEB_SEARCH`, `TAVILY_*`);
- **timestamp** (`created_at` / `updated_at`).

*Stan implementacji:* część pól jest już w telemetrii tokenów / modelach w kodzie; **wymóg produktowy** to możliwość powiązania rekordu deala z konkretnym zestawem promptów/rubryki — dopóki nie ma dedykowanych kolumn wersji, utrzymuj **changelog** w repo i oznaczaj release’y; docelowo zapisać wersje w DB lub w `run` record ([§ 28](#28-run-auditability)).

---

## 27. Evaluation harness

Przed „poważnym” użyciem narzędzia utrzymuj katalog referencyjny (np. `evals/reference_deals/` w repo — struktura do ustalenia), minimalnie:

| Kategoria | Liczba przykładów (min.) | Co weryfikujesz |
|-----------|--------------------------|-----------------|
| Oczywiste poza mandatem (Gate 1) | 5 | `FAIL_CONFIDENT` sensownie, bez deck abuse |
| In-mandate silne | 5 | Score i narracja spójne z faktami |
| Obiecujące ale niekompletne | 5 | `ASK_FOR_MORE_INFO` / review, nie absurdalny reject |
| Website-only | 5 | Klasyfikacja twierdzeń + capy ([§ 30](#30-website-screening-contract)) |
| Niski sygnał decku | 5 | Jawne missing, niski confidence |
| Duplikaty / reskan | 3 | Zachowanie dedupe ([§ 18](#18-deduplication)) |
| External / Gate 2.5 | 3 | Źródła, brak „magii” bez evidence |

Dla każdego case definiuj **oczekiwane**: próg Gate 1, kluczowe fakty, dopuszczalny zakres score na krytycznych wymiarach, oczekiwany typ `final_action` (kategoria), czy wymagany review człowieka.

**Regresja:** zmiana promptu / rubryki / modelu jest akceptowalna tylko jeśli **nie psuje** materjalnie jakości na zestawie referencyjnym (lub jeśli regresja jest świadomie zaakceptowana i udokumentowana).

---

## 28. Run auditability

Każdy screening **musi** mieć stabilny identyfikator przebiegu **`run_id`** (UUID lub monotoniczny ID), który łączy:

- źródło wejścia (mail / URL / CLI),
- tożsamość rekordu (`message_id` / `deal` row),
- modele i sufity tokenów użyte w przebiegu,
- wersje promptów / rubryki (lub referencję do commita),
- zagregowaną telemetrię tokenów i kosztów,
- zwalidowane wyjścia LLM po parsowaniu,
- ewentualne błędy i retry.

*Implementacja minimalna MVP:* identyfikator generowany na początku przebiegu w `main.py`, propagowany do logów i opcjonalnie zapisany w osobnej tabeli / polu w przyszłości; **nie** blokuje to użycia deala dziś (timestampy + telemetria już częściowo istnieją).

---

## 29. Deal lifecycle status

**`final_action`** opisuje **co robi automat / co sugeruje następny krok operacyjny**; pole **`status`** w `deals` opisuje **gdzie deal jest w maszynie stanów pipeline’u**. Oba są potrzebne.

Kanoniczna lista statusów technicznych jest w kodzie: `storage/database.py` (np. `NEW`, `GATE1_FAILED`, `GATE2_RUNNING`, `WAITING_HITL`, `GATE25_RUNNING`, `REJECTED_EXTERNAL_CHECK`, `ERROR`, `APPROVED`, …).  

**Mapowanie produktowe (intencja):**

| Intencja lifecycle | Typowe statusy techniczne (przykłady) |
|--------------------|--------------------------------------|
| Nowy / w toku | `NEW`, `GATE1_RUNNING`, `PDF_DOWNLOADED`, `GATE2_RUNNING`, `GATE25_RUNNING` |
| Odrzucony wcześnie | `GATE1_FAILED`, `REJECTED_GATE1`, `REJECTED_GATE2` |
| Przeszedł AI, czeka na człowieka | `WAITING_HITL`, `ANALYZED_WITH_DECK`, część `GATE2_INTERNAL_PASS` |
| Po człowieku | `APPROVED`, `REJECTED_HITL`, `SKIPPED`, szkice Gmail |
| Błąd systemowy | `ERROR`, `PDF_*_FAILED` |

Notion **Status** (Select) powinien być **powiązany** z tym lifecycle (mapowanie w sync — szczegóły operacyjne w `CURSOR_NOTION_INSTRUCTIONS.md`).

---

## 30. Website screening contract

Screening www **nie** jest równoważny deckowi i nie może udawać pełnego due diligence.

1. **Klasyfikacja twierdzeń** — każda istotna teza w outputcie musi być możliwa do przypisania do jednej z klas: **explicite na stronie**, **wywnioskowana ze strony**, **niedostępna na stronie**, **wzbogacona external (Gate 2.5)**.
2. **Capy przy braku dowodu** — przy braku twardego dowodu na traction / revenue / zespół / rundę: obniżona wiarygodność score / długa lista `missing` / explicite „potrzebny deck lub call”.
3. **Nie eskalować precyzji** — www-only nie wolno traktować jak pełnego scorecardu deckowego bez jawnego oznaczenia ograniczeń źródła.

**Doprecyzowanie kontraktu (wymogi jakościowe WWW):**

4. **Crawl scope (minimum)** — `assess-url` ma próbować zebrać nie tylko homepage, ale także kluczowe podstrony, nawet gdy menu jest JS-renderowane i linki nie są widoczne w surowym HTML:
   - `/about` / `/company`, `/team`, `/contact`, `/pricing`, `/customers` / case studies (o ile istnieją).
   - Jeśli strona blokuje crawl / jest SPA bez SSR: pipeline ma to jawnie odnotować (quality flags) i obniżyć confidence zamiast „udawać”, że zebrał komplet.

5. **Proper nouns / konkret** — jeżeli na stronie są nazwy własne (founderzy, klienci, integracje, standardy compliance typu SOC 2), pipeline ma je **wyciągnąć wprost** (dokładna pisownia) i pokazać jako część „evidence” / „named entities”. Brak nazw własnych przy ich obecności na stronie jest traktowany jako błąd jakości ekstrakcji (do poprawy promptu/crawla).

6. **Geography** — output WWW nie może zostawiać geo jako puste „—” bez powodu:
   - Jeśli geo jest na stronie → zapisane.
   - Jeśli geo nie jest na stronie → jawnie `unknown` (oraz opcjonalnie słaba inferencja jako `inferred`, z etykietą niepewności).

7. **Scoring bands + uzasadnienie** — żeby uniknąć „gołych liczb”, każdy wymiar WWW ma mieć:
   - score 1–10,
   - **band** zgodny ze score: 1–3 (weak), 4–6 (partial), 7–9 (strong), 10 (outlier),
   - krótkie uzasadnienie „dlaczego w tym bandzie” + „co przesunęłoby wynik w górę” (missing proof).

8. **Struktura raportu (3 podsekcje w każdej sekcji Notion)** — każda z 9 sekcji strony rekordu powinna wewnątrz akapitu mieć stałą strukturę:
   - **(1) Signal** / co system twierdzi,
   - **(2) Evidence** / na czym to stoi (cytaty / wyciąg z faktów),
   - **(3) Unknowns / Validate next** / czego brakuje i co sprawdzić dalej.
   *Uwaga techniczna:* nadal utrzymujemy 10 par bloków `heading_2`+`paragraph` (20 bloków łącznie) — podsekcje są formatowaniem tekstu w akapicie, bez dodatkowych bloków.

---

## 31. External source policy

Gate 2.5 korzysta ze snippetów i rankingów źródeł z modułu researchu — produktowo:

- każde twierdzenie z webu ma **typ źródła** (oficjalna strona, media, baza, social, inne — wg `agents/schemas_gate25.py`);
- przy **konflikcie** między źródłami: **nie rozstrzygaj automatycznie na korzyść founderów** — raportuj konflikt, obniż confidence, nie używaj jako twardego dowodu przy niskiej jakości źródła;
- **LLM-only** (bez Tavily) nie jest „ukrytym browse” — model pracuje na faktach pipeline’u + kontekście zapytania, bez świeżej walidacji sieciowej.

---

## 32. Data retention and access control

- **PDF / surowe pliki:** polityka przechowywania (lokalnie vs tylko pośrednio) — opisać w deployment guide; nie commitować decków.
- **Markdown / logi ekstrakcji:** jeśli są zapisywane (`logs/`), określ okres retencji i kto ma dostęp.
- **`pipeline.db`:** dostęp tylko dla operatorów funduszu i środowiska zaufego; backup według polityki wewnętrznej.
- **OAuth Gmail / tokeny:** wyłącznie lokalnie lub w zaufanym sekrecie środowiska produkcyjnego.
- **Usunięcie danych founderów (request):** procedura ręczna: usunięcie / anonimizacja rekordu w SQLite + Notion + ewentualne logi — do operacjonalizacji przed produkcją poza jednym laptopem.
- **External API:** przekazywać **minimalny** kontekst (fakty + plan zapytań), nie cały deck, zgodnie z `external_check`.

---

## Product summary

✅ Dwa wejścia: **deck z maila** i **WWW** → jeden rekord w **`deals`**  
✅ Mandat, fakty, scorecard, decyzje w kodzie, opcj. Gate 2.5  
✅ Sync Notion z DB  
✅ PDF → Markdown  

❌ Auto-wysyłka do founderów  
❌ Jedyny arbiter decyzji inwestycyjnej  
❌ Gwarancja prawdziwości liczb z materiałów  

---

## Appendix A — Canonical source-of-truth index

PRD **nie** duplikuje README ani pełnego drzewa plików; poniżej hierarchia dokumentów oraz szybki indeks temat → plik.

### Dokumenty w repo — hierarchia i pokrycie

| Dokument / artefakt | Co jest w nim „prawdą” | Relacja do PRD |
|---------------------|-------------------------|----------------|
| **`PRD.md`** (ten plik) | Wymagania, journeys, kryteria sukcesu; appendix canonical | Główny dokument produktowy dla MVP |
| **`ARCHITECTURE.md`** | Przepływ techniczny szczegółowy (np. pre-filter Gmail, diagram Gate 1/2), **struktura plików**, uruchomienie, `.env`, koszty | Uzupełnienie techniczne — PRD **nie** zastępuje go; brakujące szczegóły implementacji → tam |
| **`SCREENING_RUBRIC.md`** | Definicje wymiarów i wag scorecardu (deck) | PRD odsyła tu dla rubryki |
| **`LLM_SCREENING_SPEC.md`** | **Mapa pytań analitycznych** per Gate (1, 2A, 2B, 2C, 2.5, www) + drzewo | Kontrakt „co LLM musi rozstrzygnąć”; prompty w `config/prompts.py` muszą być z tym spójne |
| **`storage/database.py`** | **Schema tabeli `deals`** (kolumny, migracje), funkcje zapisu | **Źródło prawdy dla pól rekordu deala** |
| **`main.py`** | Kolejność orchestracji (`process_email`, `run_assess_url`), **`final_action`**, progi środowiskowe | **Źródło prawdy dla zachowania pipeline’u** |
| **`agents/screener.py`** | `gate1_fit_check`, `run_gate2_pipeline` | Implementacja Gate 1/2 (deck) |
| **`agents/website_screener.py`** + pipeline www | Ścieżka URL | Implementacja www |
| **`agents/notion_sync.py`** | **`_build_deal_summary_blocks`**, **`desired_headings_order`**, `_ensure_page_summary_blocks`, sync API | **Źródło prawdy dla struktury treści strony rekordu w Notion** |
| **`agents/external_check.py`** | Gate 2.5 orchestracja | Źródło prawdy dla external |
| **`agents/external_research.py`** | Tavily vs LLM-only | Źródło prawdy dla providera researchu |
| **`agents/schemas.py`** | `Gate2ScoreOutput`, `DimensionScore`, Gate 1 parsed | Kontrakt JSON LLM (deck scoring) |
| **`agents/schemas_gate25.py`** | Modele Pydantic outputu Gate 2.5 (źródła, wers, rekomendacja) | Kontrakt JSON external |
| **`config/prompts.py`** | Teksty promptów Gate 1/2 | Źródło prawdy dla treści „pytań” do modelu |
| **`config/prompts/external_*.md`** | Prompty external | Źródło prawdy dla Gate 2.5 LLM |
| **`CURSOR_NOTION_INSTRUCTIONS.md`** | Reguły operatorskie Notion (5 kolumn, upsert, kolory Status) | **Operacyjne uzupełnienie** PRD — treść strony rekordu zgodna z **§ 10** i implementacją `notion_sync.py` ([§ 1 Purpose](#1-purpose-of-this-document)) |

**Zasada rozstrzygania sporów:** patrz [§ 1 Purpose of this document](#1-purpose-of-this-document) (podsekcja *Reguła canonical*).

### Indeks tematów (co dokąd patrzeć)

| Temat | Canonical |
|-------|-----------|
| Lista kolumn deala / plik DB | `storage/database.py` (`DB_PATH` → zwykle `pipeline.db`), tabela `deals`, `save_*`, `get_deal_for_notion` |
| Kolejność: Gate 1 → PDF → Gate 2 (mail) | `main.py` → `process_email` |
| Werdykt Gate 1 / payload LLM | `agents/screener.py` + `config/prompts.py` (`GATE1_*`) |
| Ekstrakcja i scorecard deck | `agents/screener.py` `run_gate2_pipeline`, `config/prompts.py` (`GATE2A/B/C_*`) |
| Wymiary i typy pól scorecardu | `agents/schemas.py`, `SCREENING_RUBRIC.md`, `config/scoring.py` |
| Mapa pytań LLM per Gate | **`LLM_SCREENING_SPEC.md`** (kontrakt analityczny; prompty muszą być spójne) |
| Ścieżka www | `main.py` (`run_assess_url`), `agents/website_screener.py`, powiązane moduły `agents/website_*.py` |
| **`final_action`**, decyzje screeningowe | `main.py` (`process_email`, `run_assess_url`), `save_screening_decisions` |
| Gate 2.5 + Tavily (shape odpowiedzi) | `agents/schemas_gate25.py`, `agents/external_check.py`, `agents/external_research.py`, prompty `config/prompts/external_*.md`, `.env` (`TAVILY_*`, `EXTERNAL_WEB_SEARCH`) |
| Gmail OAuth / drafty | `tools/gmail_client.py`, `agents/orchestrator.py` |
| Notion: kolumny + treść podstrony (nagłówki H2, kolejność sekcji) | **`agents/notion_sync.py`** (`_build_deal_summary_blocks`, `desired_headings_order`) |
| Układ widoczny 5 kolumn (policy UI) | `CURSOR_NOTION_INSTRUCTIONS.md` § zasady + zgodność z `notion_sync` |
