from tools.legal_blocks import extract_legal_blocks_from_html


def test_extract_footer_legal_block_middlebridge():
    html = """
    <html><body>
      <footer>
        © 2025 Middle Bridge sp. z o.o. All rights reserved.
        Middle Bridge is a limited liability company registered in Lodz, Poland under KRS number 0001176237.
      </footer>
    </body></html>
    """
    blocks = extract_legal_blocks_from_html(html, "https://www.middlebridge.pl/")
    assert blocks
    txt = " ".join(b.text for b in blocks)
    assert "registered in Lodz, Poland" in txt
    assert "KRS number 0001176237" in txt
