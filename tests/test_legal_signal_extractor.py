from agents.legal_signal_extractor import LegalTextInput, extract_legal_signals


def test_middlebridge_legal_signal_extraction():
    text = (
        "© 2025 Middle Bridge sp. z o.o. All rights reserved. "
        "Middle Bridge is a limited liability company registered in Lodz, Poland under KRS number 0001176237."
    )
    out = extract_legal_signals(
        [
            LegalTextInput(
                text=text,
                source_url="https://www.middlebridge.pl/",
                source_type="official_website_footer",
            )
        ]
    )
    assert out.legal_entity_name == "Middle Bridge sp. z o.o."
    assert out.legal_form == "sp. z o.o."
    assert out.registered_city == "Lodz"
    assert out.registered_country == "Poland"
    assert any(x.type == "KRS" and x.value == "0001176237" for x in out.registry_ids)
