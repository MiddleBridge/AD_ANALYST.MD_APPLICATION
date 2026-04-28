# CURSOR INSTRUCTIONS — INOVO AI Pipeline / Notion Sync
# Wersja: 2026-04-27
# NIE MODYFIKUJ tej logiki bez świadomej decyzji

---

## 🔒 ZASADY NIEZMIENNE (nie ruszaj nigdy)

1. **Tabela Notion ma DOKŁADNIE 5 widocznych kolumn:**
   - `Company` (Title)
   - `Score` (Number)
   - `Status` (Select)
   - `Sector` (Select)
   - `Received At` (Date)
   
   WSZYSTKIE inne properties są w bazie danych Notion ale **hidden** (niewidoczne w tabeli).
   Nigdy nie dodawaj nowych widocznych kolumn do tabeli.

2. **Wszystkie szczegóły są w subpage (children blocks) — NIE w kolumnach tabeli.**
   Każdy rekord ma strukturę subpage opisaną poniżej.
   
3. **Upsert po `message_id`** — nigdy nie twórz duplikatów.
   Przy update istniejącego rekordu: aktualizuj properties, NIE nadpisuj children jeśli już istnieją
   (partner mógł dopisać notatki w Raw Notes).

---

## 📐 STRUKTURA SUBPAGE — obowiązkowa dla każdego rekordu

### Sekcja 1: ⚡ Decision Snapshot

Krótki „nagłówek” dla partnera: decyzja + progi + brakujące dane w 5–10 liniach.

### Sekcja 2: 📋 Deal Summary
```
One-liner: {product_one_liner}
Founders: {founders}
Founded: {founded_year}
Received: {received_at YYYY-MM-DD}
Website: {canonical_url lub gmail_link}
```
- Dla website flow: One-liner z `website_facts.product_description` lub `website_facts.one_liner`
- Jeśli pole puste → "not found" (nigdy pusty string ani "—")
- NIE zapisuj `Sender` jeśli to "Website Scanner <website@scanner.local>" — to śmieć techniczny
- Sender zapisuj TYLKO dla email flow (prawdziwy nadawca maila)

### Sekcja 3: 🎯 Screening Result
```
Innovo Fit: {innovo_fit_decision} (score: {innovo_fit_score})
Deck Evidence: {deck_evidence_decision} (score: {deck_evidence_score})
Generic VC Interest: {generic_vc_interest}
Final Action: {final_action}
Verdict: {verdict}
Rationale: {rationale — jedno zdanie dlaczego taka decyzja}
Screening Depth: {screening_depth}
```
- `Auth Risk` zapisuj TYLKO dla email flow. Dla website scan (`source_type == "website"`) — pomiń.
- `Rationale` to `recommended_next_step` lub `gate1_result.reasoning` lub `gate2_result.summary`

### Sekcja 4: 💪 Strengths
Każdy strength jako osobna linia:
```
• {name} ({score}/10): {description}
```
Źródło: `website_result.top_strengths` lub `gate2_result.strengths`
Jeśli brak danych → napisz "not found" (ale sprawdź WSZYSTKIE możliwe pola — patrz niżej)

### Sekcja 5: ⚠️ Risks
Każdy risk jako osobna linia:
```
• {name} ({score}/10): {description}
```
Źródło: `website_result.top_risks` lub `gate2_result.risks`

### Sekcja 6: 🏁 Market Context

Rynek, konkurencja, dynamika kategorii — krótko i bez udawania „full diligence”.

### Sekcja 7: ❓ Missing & Follow-ups
```
Missing:
• {missing_item_1}
• {missing_item_2}

Kill flags: {kill_flags jako comma-separated, lub "none"}

Follow-up questions:
• {question} — {why_it_matters}
```
Źródło: `website_result.missing_critical_data`, `website_result.kill_flags`, 
         `website_result.follow_up_questions`

### Sekcja 8: ➡️ Recommended Next Step
```
{recommended_next_step}
```

### Sekcja 9: 🔍 Raw Notes
```
— brak notatek —
```
To pole zostawia się puste dla ręcznych notatek partnera.
**NIGDY nie nadpisuj tej sekcji przy update** jeśli partner już coś wpisał.

---

## 🐛 ZNANE BUGI — napraw te konkretnie

### Bug 1: Strengths/Risks/Follow-ups = "not found"
**Problem:** Sekcje 3, 4, 5 pokazują "not found" mimo że terminal ma dane.

**Diagnoza:** Dane są w obiekcie wynikowym ale kod szuka ich pod złą nazwą klucza.

**Fix:** W `agents/notion_sync.py` sprawdź mapowanie pól. 
Przeszukaj w tej kolejności:
```python
# Strengths - sprawdź wszystkie możliwe klucze
strengths = (
    getattr(result, 'top_strengths', None) or
    getattr(result, 'strengths', None) or
    getattr(result, 'vc_pack', {}).get('top_strengths') or
    getattr(result, 'website_vc_pack', {}).get('strengths') or
    []
)

# Follow-ups
followups = (
    getattr(result, 'follow_up_questions', None) or
    getattr(result, 'follow_ups', None) or
    getattr(result, 'must_validate_next', None) or
    []
)

# Kill flags  
kill_flags = (
    getattr(result, 'kill_flags', None) or
    getattr(result, 'red_flags', None) or
    []
)
```
Dodaj `print(dir(result))` tymczasowo żeby zobaczyć dostępne atrybuty.

### Bug 2: Duplikaty Handwave (website_unknown-site + website_handwave.com)
**Problem:** Stare rekordy z legacy message_id pozostają w bazie.

**Fix dla nowych skanów** — deterministyczny message_id:
```python
import hashlib
if source_type == "website":
    message_id = "website_" + hashlib.md5(canonical_url.encode()).hexdigest()[:12]
```

**Fix dla legacy rekordów** — dodaj do README że stare rekordy z `website_unknown-site` 
należy ręcznie zarchiwizować w Notion (zmienić Status na "Archived").

### Bug 3: Company title ma prefix score [x.xx]
**Problem:** Tytuł w Notion to `[7.43] Tracelight` zamiast `Tracelight`.

**Fix:** Score jest osobną kolumną `Number`. Tytuł = tylko nazwa firmy.
```python
# ŹLE:
title = f"[{score}] {company_name}"
# DOBRZE:
title = company_name
```

---

## 📊 HIDDEN PROPERTIES (są w bazie, niewidoczne w tabeli)

Te properties MUSZĄ być zapisywane do bazy (potrzebne do pipeline logic i subpage),
ale NIE wyświetlane jako kolumny w tabeli:

```
Founders, Founded Year, Gmail Link, Message ID, Product One-liner,
Mail Subject, Sender, Auth Risk, Debug Override Used, Deck Evidence Decision,
Deck Evidence Score, External Opportunity Score, Final Action, Generic VC Interest,
Innovo Fit Decision, Innovo Fit Score, Screening Depth, Test Case
```

---

## 🔄 LOGIKA UPSERT

```python
def upsert_to_notion(record):
    existing = find_by_message_id(record.message_id)
    
    if existing:
        # Update properties
        update_properties(existing.page_id, record)
        
        # Children aktualizujemy świadomie:
        # - utrzymujemy kolejność sekcji (H2 + paragraph)
        # - aktualizujemy treści sekcji
        # - sekcji 🔍 Raw Notes NIE nadpisujemy jeśli partner coś dopisał
        #
        # Canonical implementation: `agents/notion_sync.py` → `_ensure_page_summary_blocks`.
        ensure_page_summary_blocks(existing.page_id, record)
    else:
        # Nowy rekord — stwórz z children
        create_page_with_children(record)
```

---

## 🗂️ MAPOWANIE STATUS → Notion Select + kolor

| Pipeline status | Notion Status | Kolor |
|---|---|---|
| REJECTED_GATE1 / REJECTED_GATE2 | Reject | 🔴 red |
| WAITING_HITL | Review | 🟡 yellow |
| APPROVED | Pass | 🟢 green |
| TEST_CASE_ONLY | TEST_CASE | ⚫ gray |
| SKIPPED / ERROR | Skipped | ⬜ default |

---

## 📅 FORMAT DAT

Zawsze zapisuj daty jako Notion `date` property (nie text):
```python
from datetime import datetime
date_str = datetime.fromisoformat(received_at).strftime("%Y-%m-%d")
# → Notion: {"date": {"start": "2026-04-27"}}
```

---

## ✅ CHECKLIST przed każdym PR dotyczącym notion_sync.py

- [ ] Tabela ma dokładnie 5 widocznych kolumn?
- [ ] Company title nie ma prefiksu score?
- [ ] Subpage ma wszystkie 9 sekcji (H2 + paragraph)?
- [ ] Strengths/Risks/Follow-ups nie pokazują "not found" dla rekordów z danymi?
- [ ] Auth Risk i Sender są pomijane dla website scans?
- [ ] Upsert nie nadpisuje Raw Notes jeśli partner już coś wpisał?
- [ ] message_id dla website scans to hash URL (nie "website_unknown-site")?
- [ ] Daty są w formacie YYYY-MM-DD jako Notion date property?
