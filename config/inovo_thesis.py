"""Inovo-specific investment thesis constants (single-fund engine)."""

INOVO_GEO_CORE = [
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

INOVO_GEO_ACCEPTED_SIGNALS = [
    "hq_in_cee",
    "founder_from_cee",
    "founder_educated_in_cee",
    "founder_worked_in_cee",
    "company_started_in_cee",
    "engineering_team_in_cee",
    "meaningful_operations_in_cee",
    "cee_diaspora_founder",
]

INOVO_STAGE_CORE = [
    "pre-seed",
    "seed",
    "seed-extension",
]

INOVO_STAGE_ACCEPTABLE_BUT_NEEDS_CHECK = [
    "late-seed",
    "series-a-ready",
    "series-a",
]

INOVO_SECTORS_STRONG = [
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

INOVO_SECTORS_WEAK_OR_RISKY = [
    "crypto",
    "consumer social",
    "education",
    "pure marketplace",
    "services-heavy business",
    "consulting",
    "agency",
]

INOVO_TICKET_MIN_EUR = 500_000
INOVO_TICKET_MAX_EUR = 4_000_000

INOVO_HARD_BLOCKERS = [
    "no_software_component",
    "too_late_stage",
    "no_cee_link_confirmed_after_enrichment",
    "pure_services_business",
    "outside_ticket_range",
    "sanctions_or_illegal_activity_risk",
    "regulatory_status_incompatible",
]

