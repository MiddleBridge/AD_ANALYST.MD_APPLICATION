================================================================================
INOVO.VC — TOP HIGH-VALUE USE CASES (wersja aplikacyjna)
Opis pod role: Analyst (Sourcing & Ops)
Wygenerowano: 2026-04-28
================================================================================

0) TL;DR
--------
Ten system to operacyjny copilot dla partnera VC, który:
  • przyspiesza screening inboundu (email/deck + website),
  • zamienia chaos wejścia w decyzje i kolejne kroki,
  • utrzymuje jeden operacyjny widok w Notion,
  • zostawia człowieka na kluczowych decyzjach (HITL).

Poniżej są WYRAŹNIE wydzielone use case'y i ich skład.


1) USE CASE 1 — Inbound founder screening (email/deck) end-to-end
------------------------------------------------------------------
Problem:
  Dużo inboundu, mało czasu partnera. Ręczny triage spowalnia sourcing.

Wartość biznesowa:
  Szybszy first screen, mniej "dropped balls", spójna jakość oceny.

Skład use case (z czego się składa):
  A) Input:
     - Gmail OAuth2 + maile z pitch deckiem PDF.
  B) Processing:
     - Gate 0: tani prefilter (non-pitch/legal).
     - Gate 1: szybki fit check (geografia/stage/sektor + confidence).
     - Gate 2: analiza decka (fakty, scorecard, ryzyka, summary).
     - Gate 3: HITL (Approve/Reject/Skip).
  C) Output:
     - zapis rekordu do pipeline.db,
     - brief dla partnera,
     - opcjonalnie draft odpowiedzi w Gmailu (bez auto-send).
  D) Guardrails:
     - idempotencja po message_id,
     - limity PDF i tokenów,
     - decyzja inwestycyjna finalnie po stronie człowieka.

Kluczowe moduły:
  main.py, agents/screener.py, tools/gmail_client.py, tools/pdf_utils.py,
  hitl/terminal.py, storage/database.py


2) USE CASE 2 — Decision support + pipeline memory (Notion sync)
-----------------------------------------------------------------
Problem:
  Nawet dobry screening traci wartość, jeśli wynik nie trafia do czytelnego systemu operacyjnego.

Wartość biznesowa:
  Partner ma "decision-ready view" w jednym miejscu: snapshot, fit, ryzyka, follow-ups.

Skład use case:
  A) Input:
     - rekordy z pipeline.db (wyniki Gate 1/2/2.5 + decyzje).
  B) Processing:
     - mapowanie pól do Notion DB (upsert po Message ID),
     - budowa sekcji strony deala (decision, fit, snapshot, risks, open questions, evidence),
     - deterministyczna aktualizacja bez dodatkowego LLM na etapie sync.
  C) Output:
     - tabela operacyjna Notion (status + score + sektor + data),
     - strona firmy z pełnym memo inwestycyjnym.
  D) Guardrails:
     - idempotent update/create,
     - ochrona manualnych notatek partnera,
     - schema-safe mapowanie właściwości.

Kluczowe moduły:
  agents/notion_sync.py, storage/database.py


3) USE CASE 3 — Reporting i operating cadence (weekly ops loop)
----------------------------------------------------------------
Problem:
  Bez szybkiego raportowania trudno zarządzać lejkiem i priorytetami.

Wartość biznesowa:
  Krótsza pętla feedbacku partner <-> pipeline, lepsze priorytetyzowanie top deali.

Skład use case:
  A) Input:
     - historyczne rekordy z pipeline.db.
  B) Processing:
     - agregacja funnel stats (statusy, sektory, geografie, top deale),
     - raport per okres (np. 7 dni).
  C) Output:
     - tekstowy raport operacyjny do szybkiego przeglądu.
  D) Guardrails:
     - raport oparty o ten sam data model co screening,
     - możliwość audytu po message_id.

Kluczowe moduły:
  main.py (--report), agents/reporter.py, storage/database.py


4) Dlaczego ten zestaw use case'ów jest "high-value" dla roli Analyst (Sourcing & Ops)
---------------------------------------------------------------------------------------
  • łączy sourcing + first screens + decision support + ops hygiene w jednym workflow,
  • wzmacnia "same-day loops" i ogranicza operacyjny chaos,
  • tworzy reusable templates (briefy, statusy, follow-ups, notatki z calli),
  • jest AI-native, ale z bezpieczną granicą człowieka na finalnej decyzji.


5) Krótka mapa komend (dowód działania)
---------------------------------------
  python main.py --once
      Jednorazowy screening inboundu email/deck.

  python main.py assess-url https://example.com
      Screening website-only.

  python main.py --report --days 7
      Raport operacyjny pipeline.

  python main.py --sync-notion --days 30
      Synchronizacja rekordów do Notion.


6) Szczegóły operacyjne UC3 (Weekly report) — jak to czytać
-----------------------------------------------------------
Poziom użyteczności:
  Tak, raport jest useful jako weekly heartbeat operacyjny.
  To nie jest jeszcze "partner memo", tylko narzędzie do zarządzania lejkiem.

Co raport daje dzisiaj:
  A) Lejek:
     - gdzie odpada inbound (Gate 1 vs Gate 2 vs error),
     - ile realnie przeszło dalej.
  B) Priorytety:
     - top deals po score,
     - lista "consider now" vs "not fit now".
  C) Jakość operacji:
     - udział PDF/extraction fail,
     - udział błędów pipeline.
  D) Efektywność:
     - szacowany koszt API,
     - średnie latency Gate 1 i Gate 2.

Jak interpretować obecny sample raport:
  - 31 inbound / 13 odrzuceń na Gate 2 (41%) -> główny bottleneck jakości jest po analizie decka.
  - 0 approved -> brak deali gotowych do finalnego "yes" w tym tygodniu.
  - duży udział "NEEDS_DECK" i rekordów "unknown" -> problem kompletności inputu i higieny danych.
  - score top deali < 6/10 -> nie ma oczywistego shortlista "must-call".

Obecne ograniczenia (ważne do komunikacji):
  - duplikaty firm w rankingu (np. wiele wpisów tej samej spółki),
  - niespójne etykiety sektorów/geografii (np. Fintech vs fintech),
  - rekordy testowe/placeholdery mieszają obraz (unknown, Example Domain),
  - brak sekcji "actionable next steps" per firma.

Co podnosi wartość raportu do poziomu partner-grade (kolejny krok):
  1. Deduplikacja po company/domain + normalizacja kategorii.
  2. Odfiltrowanie testów i pustych rekordów z weekly view.
  3. Sekcja "Top 5 actions this week":
     - Request deck,
     - Founder call,
     - Reject with reason,
     - Follow-up owner + deadline.
  4. Delta week-over-week:
     - zmiana conversion i kosztu/deal.


7) Szczegóły UC3 Meeting Notes (Fireflies/Calendly -> Notion)
--------------------------------------------------------------
Poziom użyteczności:
  UC3 domyka pętlę po rozmowie z founderem: każda rozmowa jest dopinana do strony firmy.

Co jest wdrożone:
  - one-shot sync calla do Notion: `python main.py sync-call ...`
  - sekcja `Founder Calls` na stronie firmy,
  - deduplikacja po `call_id`,
  - opcjonalne tworzenie tasków w osobnej bazie (`NOTION_TASKS_DATABASE_ID`),
  - wiele rozmów tej samej firmy ląduje na jednej stronie firmy (jeden "subpage" deala).

Format wpisu calla:
  - nagłówek rozmowy (`YYYY-MM-DD — title (source)`),
  - bullet: data + attendees,
  - bullet: summary,
  - bullet: link do transcriptu/nagrania.

Zakres "auto webhook" (następny krok):
  - obecny tryb to one-shot CLI trigger,
  - webhook Fireflies/Calendly można podpiąć do tego samego mechanizmu bez zmiany modelu danych.


================================================================================
Koniec pliku. Szczegóły techniczne i pełny opis architektury:
patrz README.md oraz PRD.md w tym samym repozytorium.
================================================================================
