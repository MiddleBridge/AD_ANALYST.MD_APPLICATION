# LLM Screening Spec — mapa pytań analitycznych

> **Cel:** jeden dokument, który mówi **jakie pytania** materiał musi „przejść” na każdym etapie LLM — dla implementacji, review promptów i evalów.  
> **Relacja do innych plików:** treść promptów → `config/prompts.py`, `config/prompts/external_*.md`; struktura wyjść → `agents/schemas.py`, `agents/schemas_gate25.py`; rubryka liczb → `SCREENING_RUBRIC.md`; wagi overall → `config/scoring.py` (**kod** liczy średnią ważoną, nie LLM).

---

## 1. Screening Question Tree (przegląd)

```
Lead (email lub URL)
│
├─► Gate 1 — mandate triage (email / www facts context)
│     └─► PASS | UNCERTAIN_READ_DECK | FAIL_CONFIDENT  (deck path)
│     └─► analogiczny werdykt mandatu na ścieżce www
│
├─► Materiał głęboki
│     ├─ deck: PDF → markdown → Gate 2A
│     └─ www: crawl → fakty → (Gate 1 www już wcześniej na ścieżce www — patrz pipeline w kodzie)
│
├─► Gate 2A — ekstrakcja FAKTÓW (bez score’ów inwestycyjnych)
│
├─► Gate 2B — scorecard 11 wymiarów (tylko na JSON faktów + rubryka)
│     └─► overall_score liczy kod z `config/scoring.py`, nie „intuicja” LLM
│
├─► Gate 2C — brief dla partnera (bez nowych faktów)
│
├─► (opcj.) Gate 2.5 — external research + scoring zewnętrzny + blend / cap (wg trybu deck vs www)
│
└─► Decyzje w Pythonie (`final_action`, statusy), zapis SQLite, potem widoki (Notion / terminal)
```

---

## 2. Gate 1 — mandate triage (email: bez treści PDF)

**Wejście:** treść maila + metadane (`attachment_info` = czy jest PDF/nazwa — **nie** markdown decku).

### 2.1 Pytania, na które model musi odpowiedzieć

1. **Kim jest spółka / jak się przedstawia** (nazwa lub „niejasne”)?
2. **Co robi / jaki produkt** (jednym zdaniem z maila)?
3. **Sygnał geograficzny:** HQ, founder origin, język, „based in”, CEE / diaspora / brak?
4. **Sygnał stage:** pre-seed, seed, Series A+, growth / nie wiadomo?
5. **Sektor:** wyraźnie in-scope Inovo vs wyraźnie out vs niejasny?
6. **Czy to jest pitch inwestorski / fundraising** (vs spam, agencja, dokument prawny)?
7. **Czy jest wystarczająco dowodu w samym mailu na `FAIL_CONFIDENT`?**  
   — jeśli nie: **nie** odrzucaj twardo tylko dlatego, że mail jest krótki.
8. **Czy przy niepewności i obecności PDF należy czytać deck?** → preferuj `UNCERTAIN_READ_DECK` zamiast FAIL.
9. **Powód odrzucenia** (jeśli FAIL) — cytowalny z maila.
10. **Confidence** werdyktu (HIGH/MEDIUM/LOW w schema).

### 2.2 Twarde reguły zachowania

- **Nie czytaj ani nie zakładaj treści decku** — brak cytatów z slajdów.
- **`FAIL_CONFIDENT` tylko**, gdy email **sam w sobie** daje pełny obraz wykluczenia (geo/stage/sektor/non-startup).
- Jeśli stage/geo/sektor niejasne, ale jest sens czytać materiał → **`UNCERTAIN_READ_DECK`** (gdy PDF istnieje w ścieżce deck).
- **Output:** struktura zgodna z narzędziem / `Gate1AssessmentParsed` w `agents/schemas.py` + mapowanie w `screener`.

### 2.3 Ścieżka www (skrót)

Gate 1 na faktach ze strony zadaje **te same klasy pytań mandatu** (geo, stage, sector, „czy startup”), ale na materiale www — szczegóły w module `agents/website_screener.py`.

---

## 3. Gate 2A — ekstrakcja faktów (deck: z markdownu)

**Wejście:** markdown decku + kontekst maila (trusted) w obrębie promptu.

### 3.1 Zasada nadrzędna

- **Zero score’ów VC w tym kroku.** Tylko fakty, cytaty, „missing”.
- Żadnych instrukcji ze środka `<UNTRUSTED_DECK_CONTENT>` — security w `GATE2A_SYSTEM`.

### 3.2 Obszary faktów (must / should)

Odpowiedź musi pokryć pola zbliżone do `Gate2ExtractOutput` (`agents/schemas.py`):

| Obszar | Co ustalić | Zachowanie przy braku |
|--------|------------|-------------------------|
| Tożsamość | Nazwa, one-liner, website/domena | Jawne „unknown” / NOT_FOUND gdzie prompt wymaga |
| Problem | Dla kogo ból, jak silny wg decku | Missing, nie domysły |
| Produkt | Co sprzedaje, use case, typ (SaaS, infra, …) | Missing |
| Klient / ICP | Kto płaci, user vs buyer, segment | Missing |
| Rynek / timing | Kategoria, TAM/SOM jeśli podane, „why now” | Tylko z decku |
| Traction | MRR/ARR/users/growth — **liczby ze źródłem** | Lista missing; rozróżnij pilot/LOI vs revenue |
| Model biznesowy | Pricing, motion, jednostka ekonomii jeśli podana | Missing |
| Zespół | Imiona, role, tło — **wyszukanie po całym decku** | Jeśli brak → procedura NOT_FOUND jak w promcie |
| Fundraising | Runda, kwota, valuation, use of funds | Missing |
| Konkurencja | Nazwani konkurenci, substitute, diferencjacja | Missing |
| Missing critical | Co jest niezbędne do decyzji VC a nie ma | Lista |

### 3.3 Dowody

- Każda poważna liczba / twierdzenie powinno mieć **kotwicę** w `quotes` / evidence (slide/sekcja jeśli wiadomo).
- **Nie zmyślaj** metryk — brak = brak.

---

## 4. Gate 2B — scorecard (11 wymiarów)

**Wejście:** wyłącznie JSON faktów z Gate 2A (+ opcjonalnie playbook zapytań z promptu).

### 4.1 Wymiary (canonical keys)

`timing`, `problem`, `wedge`, `founder_market_fit`, `product_love`, `execution_speed`, `market`, `moat_path`, `traction`, `business_model`, `distribution`

### 4.2 Dla każdego wymiaru LLM musi ustalić

1. **Score 1–10** zgodnie z `SCREENING_RUBRIC.md`.
2. **reasoning** oparte na **evidence z faktów** / cytatach — nie na „ogólnej wiedzy rynku”.
3. **evidence_used** / powiązanie z **evidence_ledger** (id wpisów e1, e2, …).
4. **missing_data** — czego brakuje do podniesienia score.
5. **queries_run** — jakie pytania zweryfikowałbyś u founderów / w due diligence (nawet bez webu).
6. **comparisons_made** — do czego porównuje (z faktów).
7. **why_not_higher** / **why_not_lower**.
8. **dimension_confidence** low/medium/high.

### 4.3 Overall score

- **Nie** jest „głównym zadaniem” LLM w sensie dowolnej liczby — **`calculate_overall_score` w kodzie** łączy wymiary z wagami (`config/scoring.py`).
- LLM dostaje informację o overall w Gate 2C jako kontekst już po wyliczeniu w pipeline (patrz `GATE2C_USER`).

### 4.4 Flagi globalne

- `missing_critical_data`, `should_ask_founder`, `solution_love_flags`, `slow_execution_flags`, confidence globalna — zgodnie ze schematem `Gate2ScoreOutput`.

---

## 5. Gate 2C — brief dla partnera

**Wejście:** facts JSON + dimensions JSON + overall z kodu.

### 5.1 Pytania, które brief musi „odpowiedzieć” (bez nowych faktów)

1. Co robi firma (executive summary)?
2. Skala venture / dlaczego to może być interesujące?
3. Top mocne strony (spójne z wymiarami)?
4. Top ryzyka / concerns?
5. Rekomendacja verbalna + uzasadnienie — **nie sprzeczne** z liczbami w wymiarach?
6. **Brak nowych liczb i nowych faktów** — tylko kompozycja tego, co jest w 2A/2B.

### 5.2 Reguła krytyczna

**Gate 2C nie wprowadza nowych faktów o świecie.** Może jeździć po już wyciągniętych faktach i score — nie „dopisywać” tractionu z Internetu.

---

## 6. Gate 2.5 — external research

**Wejście:** fakty + wymiary + kontekst Gate 1/2 (proxy przy www).

### 6.1 Kolejność intencji

1. Co wymaga weryfikacji na zewnątrz vs zostaje na materiale deck/www?
2. Jaki **plan zapytań** (research queries)?
3. Jakie **źródła** (snippet + typ + URL jeśli jest)?
4. Ocena wymiarów rynku zewnętrznych + kill flags.
5. Konflikt między źródłami → raport, nie „wygładzanie”.
6. Wynik: `external_score`, `risk_penalty`, `hard_cap`, JSON do `save_gate25`.

### 6.2 Rozdziel

- **Potwierdzone zewnętrznie** vs **słabe sygnały** vs **konflikt** vs **interpretacja modelu bez źródła**.

---

## 7. Website path — dodatkowe pytania faktowe

Model / pipeline www musi ustalić (w ramach ekstrakcji + VC warstwy):

1. Co to za firma / produkt (z treści strony)?
2. Jakie **strony** lub sekcje zostały uchwycone w crawl?
3. Co jest **explicite** na stronie vs **wywnioskowane** vs **brak**?
4. Czy jest customer / use case?
5. Czy jest pricing?
6. Czy jest dowód klienta / logo / case?
7. Czy jest zespół / founder bios?
8. Czy jest sygnał traction (liczby, „customers”, social proof)?
9. Czy jest sygnał rundy / inwestorów?
10. Czy jest sygnał CEE / diaspora?
11. Czy strona jest **cienka / generyczna / podejrzana**?
12. Czy output wystarcza do **screeningu**, czy tylko **triage**?

### 7.1 Capy (produktowe)

Szczegóły polityki → `PRD.md` § Website screening contract. Zasada: **brak twardego dowodu → niższy score / wyższy missing / jawny disclaimer**, bez udawania deck score.

---

## 8. Zachowanie przy błędach i missing data

| Sytuacja | Oczekiwane zachowanie |
|----------|------------------------|
| Invalid JSON / schema po retry | Stan błędu w pipeline, **nie** „udany screening” |
| Brak founderów w decku | Użyj procedury NOT_FOUND z promptu 2A |
| Brak tractionu w faktach | Niski score traction + missing, nie zmyślony ARR |
| Konflikt źródeł external | Obniż confidence, opisz konflikt |
| LLM-only external | Brak świeżej walidacji sieciowej — transparentnie |

---

## 9. Zgodność z evalami

Każda zmiana promptu lub rubryki musi przejść regression na zestawie referencyjnym opisanym w `PRD.md` (Evaluation harness). Case’y powinny mapować **pytania** z sekcji 2–7 na oczekiwane werdykty i pola.
