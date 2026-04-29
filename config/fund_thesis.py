"""Fund-specific investment thesis constants (single-fund engine)."""

FUND_GEO_CORE = [
    "Poland",
    "Lithuania",
    "Latvia",
    "Estonia",
    "Czech Republic",
    "Slovakia",
    "Hungary",
    "Romania",
    "Bulgaria",
    "Slovenia",
    "Croatia",
    "Serbia",
    "Ukraine",
    "North Macedonia",
    "Bosnia and Herzegovina",
    "Kosovo",
    "Albania",
    "Montenegro",
]

FUND_GEO_ACCEPTED_SIGNALS = [
    "hq_in_cee",
    "founder_from_cee",
    "founder_educated_in_cee",
    "founder_worked_in_cee",
    "company_started_in_cee",
    "engineering_team_in_cee",
    "meaningful_operations_in_cee",
    "cee_diaspora_founder",
]

FUND_STAGE_CORE = [
    "pre-seed",
    "seed",
    "seed-extension",
]

FUND_STAGE_ACCEPTABLE_BUT_NEEDS_CHECK = [
    "late-seed",
    "series-a-ready",
    "series-a",
]

FUND_SECTORS_STRONG = [
    "developer tools",
    "software infrastructure",
    "ai/ml",
    "data infrastructure",
    "healthcare software",
    "b2b saas",
    "enterprise software",
    "fintech infrastructure",
    "workflow automation",
    "vertical ai",
    "marketplace with software layer",
]

FUND_SECTORS_WEAK_OR_RISKY = [
    "crypto",
    "consumer social",
    "education",
    "pure marketplace",
    "services-heavy business",
    "consulting",
    "agency",
]

FUND_TICKET_MIN_EUR = 500_000
FUND_TICKET_MAX_EUR = 4_000_000

FUND_HARD_BLOCKERS = [
    "no_software_component",
    "too_late_stage",
    "no_cee_link_confirmed_after_enrichment",
    "pure_services_business",
    "outside_ticket_range",
    "sanctions_or_illegal_activity_risk",
    "regulatory_status_incompatible",
]

