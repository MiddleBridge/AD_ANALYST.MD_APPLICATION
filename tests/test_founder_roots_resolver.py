from agents.founder_roots_resolver import is_valid_person_name


def test_founder_entity_filtering_rejects_company_name():
    assert is_valid_person_name("RiskSeal", "RiskSeal") is False
    assert is_valid_person_name("RiskSeal Inc", "RiskSeal") is False
    assert is_valid_person_name("Jan Kowalski", "RiskSeal") is True

