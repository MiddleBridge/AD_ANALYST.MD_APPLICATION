# INOVO screening rubric (Gate 2)

Scores must follow this document, **not** intuition alone. For every dimension the model must: identify evidence; list missing data; compare against category benchmarks or competitors where possible; explain why the score is not higher and not lower; apply caps when required; lower `dimension_confidence` when evidence is thin.

Evidence is classified in the **evidence ledger** as `deck`, `website`, `external_search`, or `llm_inference`. Prefer deck and search-backed claims over inference.

---

## Dimension: market

### What to extract

- ICP (ideal customer profile)
- Buyer persona
- ACV / pricing signals
- Market category
- Number of potential customers (TAM / SAM hints)
- Current substitutes
- Budget owner

### What to search

- `"{company} pricing"`
- `"{company} customers"`
- `"{category} market size"`
- `"{category} growth rate"`
- `"{category} competitors"`
- `"{category} funding startups"`
- `"{category} G2 alternatives"`
- `"{category} Gartner Forrester report"`

### What to compare against

- Public comps
- Funded competitors
- Typical ACV in category
- Number of buyers
- Market growth
- Saturation level

### Score rules

- **1–3:** Unclear buyer, tiny niche, no budget
- **4–5:** Real problem but weak market proof
- **6–7:** Credible market, identifiable buyer, plausible $100M revenue path
- **8–9:** Large growing market, strong tailwind, public comps
- **10:** Massive category shift; winner can be huge

### Caps (deterministic checks also applied in code)

- No ICP → max **5.5**
- No budget owner → max **6.0**
- No credible $100M revenue path → max **6.0**
- Highly saturated category with no edge → max **6.5** (see competition / moat)

---

## Dimension: competition

*Not a separate numeric column in the scorecard; it informs **market** and **moat_path**.*

### What to extract

- Named competitors and substitutes
- Funding stage of alternatives
- Incumbent vs startup field
- Differentiation claims vs reality in materials

### What to search

- `"{company} vs {competitor}"`
- `"{category} competitive landscape"`
- `"{category} market map"`
- G2 / Gartner positioning for category

### What to compare against

- Density of funded startups in same wedge
- Incumbent bundle risk
- Typical time-to-replicate for the feature set described

### Score rules (use when calibrating market / moat)

- **1–3:** Commodity space, no clear win narrative
- **4–6:** Crowded but plausible niche
- **7–8:** Credible differentiation vs named players
- **9–10:** Rare structural advantage vs incumbents

### Caps

- High saturation **and** no articulated company edge → market capped (see code: **6** on integer scale)
- High incumbent risk **and** weak moat_path score → moat_path capped (see code)

---

## Dimension: distribution

### What to extract

- Primary acquisition channel(s)
- Repeatability of motion (founder-led vs playbook)
- CAC / payback proxies if stated
- PLG vs sales-led vs partnerships vs community
- Sales cycle hints

### What to search

- `"{company} go-to-market"`
- `"{company} sales motion"`
- `"{company} partnerships"`
- `"{category} typical CAC"`

### What to compare against

- Category norms (PLG vs enterprise sales)
- Comparable stage GTM for same ACV band

### Score rules

- **1–3:** Pure founder heroics, no channel
- **4–5:** Early experiments, unclear repeatability
- **6–7:** One channel showing traction
- **8–9:** Multi-channel or exceptional unit economics proof
- **10:** Scalable machine at seed-stage rarity

### Caps

- If traction is high but distribution evidence is only “viral tweet” with no process → lower confidence and cap distribution conservatively.

---

## Dimension: traction

### What to extract

- Revenue / MRR / ARR / pilots with $
- Logos, retention, NRR if claimed
- Growth rate, hiring, usage metrics

### What to search

- `"{company} revenue"`
- `"{company} customers"`
- `"{company} case study"`

### What to compare against

- Stage-appropriate benchmarks (pre-seed vs seed)
- Vanity vs paid usage

### Score rules

- **1–3:** Idea / deck only
- **4–5:** Pilots / LOIs without economic proof
- **6–7:** Paid customers or credible usage scale
- **8–9:** Strong growth + retention signals
- **10:** Exceptional for stage

### Caps

- Waitlist / LOI-only without revenue language → do not score in 8–10 range; flag in `missing_data`.

---

## Dimension: problem

### What to extract

- Who suffers, how often, how expensive
- Workflow or budget tied to problem
- Why now (regulation, cost, tech shift)

### What to search

- `"{category} buyer pain"`
- `"{category} budget line item"`

### What to compare against

- Incumbent “good enough” solutions
- Urgency vs nice-to-have in category

### Score rules

- **1–3:** Nice-to-have, vague persona
- **4–5:** Real pain, weak quantification
- **6–7:** Clear P&L or operational pain
- **8–10:** Mission-critical, budgeted, urgent

---

## Dimension: wedge

### What to extract

- Why this team / product can win now
- Technical or distribution wedge vs status quo
- 10x claim vs evidence

### What to compare against

- Substitutes and “build in-house” option

### Score rules

- Low scores: generic AI wrapper language without workflow ownership
- High scores: specific, defensible wedge with proof in facts

---

## Dimension: founder_market_fit

### What to extract

- Relevant operator experience
- Network into buyers
- Prior exits or domain depth

### What to search

- `"{founder} linkedin"`
- `"{founder} background"`

### What to compare against

- Typical founder profile for category winners

---

## Dimension: product_love

### What to extract

- NPS, engagement, expansion, community
- Design partner depth vs logo slide

### What to compare against

- Category standards for “lovable” MVP at stage

---

## Dimension: execution_speed

### What to extract

- Shipping cadence, roadmap discipline
- Hiring plan vs burn
- Time to key milestones

---

## Dimension: business_model

### What to extract

- Pricing model, gross margin hints
- Expansion / upsell path

### What to compare against

- ACV and margin norms for same model (SaaS, usage, marketplace)

---

## Dimension: moat_path

### What to extract

- Data network effects, workflow lock-in, regulatory moats
- Switching costs

### What to compare against

- Incumbent copy risk (see competition)

---

## Dimension: timing

### What to extract

- Regulatory, technology, budget cycles
- Why incumbents are slow now

### What to compare against

- Prior false starts in category

---

## Dimension: technical_depth

*Folded into **product_love**, **moat_path**, and **execution_speed** in the 11-dimension scorecard; still evaluate explicitly when scoring those rows.*

### What to extract

- Architecture that is hard to replicate
- Team depth (PhDs, infra, safety, domain models)
- Benchmarks or third-party validation

### What to search

- `"{company} engineering blog"`
- `"{company} security compliance"`

### Score rules

- Shallow wrapper on generic API → caps moat_path and product_love confidence

---

## Global rules

1. Do **not** assign a score from intuition alone.
2. If evidence is insufficient, **lower confidence** and **cap** the score.
3. Every dimension row must include **`why_not_higher`** and **`why_not_lower`**.
4. Populate **`queries_run`** even when browsing is unavailable — phrase as real searches you would run.
5. Link dimensions to **`evidence_ledger`** item ids where possible.

See also `agents/research_playbook.py` for query templates stored in pipeline output.
