from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
import pymupdf4llm

MAX_MARKDOWN_CHARS = int(os.getenv("MAX_MARKDOWN_CHARS", "60000"))
PDF_OCR_MAX_PAGES = int(os.getenv("PDF_OCR_MAX_PAGES", "40"))
PDF_OCR_DPI = int(os.getenv("PDF_OCR_DPI", "180"))
# auto = OCR tylko gdy ekstrakcja tekstowa jest słaba; never = wyłącz; always = zawsze porównaj z OCR
PDF_OCR_MODE = os.getenv("PDF_OCR_MODE", "auto").strip().lower()
TESSERACT_LANG = os.getenv("TESSERACT_LANG", "eng")


class PDFExtractionError(Exception):
    """Raised when local PDF → Markdown conversion fails."""


class OCRNotAvailableError(Exception):
    """Tesseract / pytesseract missing or not on PATH."""


def _truncate(md: str) -> str:
    if len(md) > MAX_MARKDOWN_CHARS:
        md = md[:MAX_MARKDOWN_CHARS]
        md += "\n\n[DECK TRUNCATED — exceeded character limit]"
    return md


def _unique_word_count(text: str) -> int:
    words = re.findall(r"[\w']+", text.lower())
    return len(set(words))


def _ocr_pdf_pages(pdf_bytes: bytes) -> str:
    """Render each page to a bitmap and run Tesseract OCR."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise OCRNotAvailableError(
            "Zainstaluj pakiety: pip install pytesseract Pillow. "
            "Na macOS: brew install tesseract (opcjonalnie: brew install tesseract-lang)."
        ) from e

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts: list[str] = []
    try:
        n = min(doc.page_count, PDF_OCR_MAX_PAGES)
        for i in range(n):
            page = doc.load_page(i)
            pix = page.get_pixmap(dpi=PDF_OCR_DPI)
            img_bytes = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_bytes))
            try:
                text = pytesseract.image_to_string(img, lang=TESSERACT_LANG)
            except pytesseract.TesseractNotFoundError as e:
                raise OCRNotAvailableError(
                    "Brak programu Tesseract na PATH. macOS: brew install tesseract "
                    "(ew. export TESSDATA_PREFIX=...)."
                ) from e
            if text and text.strip():
                parts.append(f"### Slajd / strona {i + 1}\n\n{text.strip()}")
    finally:
        doc.close()

    return "\n\n".join(parts)


def pdf_bytes_to_markdown(pdf_bytes: bytes) -> str:
    """
    Najpierw pymupdf4llm (szybko, zachowuje strukturę tam gdzie jest warstwa tekstu).
    Jeśli wynik jest pusty / podejrzanie powtarzalny (typowe decki „obrazkowe”),
    albo PDF_OCR_MODE=always — uruchamiany jest OCR (render + Tesseract).
    """
    tmp = Path("/tmp/fund_deck.pdf")
    tmp.write_bytes(pdf_bytes)
    try:
        primary = pymupdf4llm.to_markdown(str(tmp))
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise PDFExtractionError(str(e)) from e
    tmp.unlink(missing_ok=True)

    ocr_disabled = os.getenv("PDF_OCR_DISABLE", "").lower() in ("1", "true", "yes")
    quality_issue = assess_deck_markdown_quality(primary) is not None
    want_ocr = not ocr_disabled and PDF_OCR_MODE != "never" and (
        PDF_OCR_MODE == "always" or quality_issue
    )

    final = primary
    if want_ocr:
        try:
            ocr_text = _ocr_pdf_pages(pdf_bytes)
        except OCRNotAvailableError as e:
            ocr_text = ""
            if quality_issue or PDF_OCR_MODE == "always":
                print(f"[Fund PDF] OCR niedostępny: {e}", file=sys.stderr)

        if ocr_text.strip():
            if PDF_OCR_MODE == "always" and not quality_issue:
                # Oba przebiegi — wybierz bogatszy w unikalne słowa
                if _unique_word_count(ocr_text) > _unique_word_count(primary) * 0.85:
                    final = (
                        "## Treść z OCR (render slajdów jako obrazy)\n\n"
                        + ocr_text
                    )
                else:
                    final = primary
            else:
                # Słaba ekstrakcja tekstowa — preferuj OCR
                final = (
                    "## Treść z OCR (tekst widoczny na slajdach jako obraz — pymupdf4llm miał mało sygnału)\n\n"
                    + ocr_text
                )
        elif quality_issue:
            # Bez Tesseract zostaje słaby primary; nie rzucamy wyjątku
            final = (
                primary
                + "\n\n[OCR: nie uruchomiono lub brak tekstu — zainstaluj Tesseract, patrz README / PDF_OCR_*]\n"
            )

    return _truncate(final)


def assess_deck_markdown_quality(md: str) -> str | None:
    """
    Return a short user-facing warning if extracted text is empty, tiny, or highly repetitive
    (common for image-only / scanned decks). None if OK-ish.
    """
    if not md or not md.strip():
        return (
            "Ekstrakcja PDF jest pusta — Gate 2 nie widzi treści decka (często skan lub sam obraz). "
            "Wynik liczbowy nie jest miarodajny."
        )
    lines = [ln.strip() for ln in md.splitlines() if ln.strip()]
    if len(lines) < 3 and len(md) < 400:
        return (
            "Z PDF wyciągnięto bardzo mało tekstu — oceny mogą być arbitralnie niskie. "
            "Sprawdź logs/*_extracted.md i rozważ PDF z warstwą tekstu lub OCR."
        )
    unique = len(set(lines))
    if len(lines) >= 12 and unique <= 3:
        return (
            "Tekst z PDF to głównie powtórzenia (np. sam nagłówek/stopka) — typowe dla decków „obrazkowych”. "
            "Model widzi prawie zero merytoryki ze slajdów; wynik Gate 2 może być bezużyteczny."
        )
    return None


def build_pdf_content_block(pdf_bytes: bytes) -> dict:
    """Return a text content block with extracted Markdown for OpenAI."""
    md = pdf_bytes_to_markdown(pdf_bytes)
    char_count = len(md)
    approx_tokens = char_count // 4
    return {
        "type": "text",
        "text": (
            f"\n\n--- PITCH DECK (extracted as Markdown) ---\n"
            f"[{char_count:,} chars, ~{approx_tokens:,} tokens]\n\n"
            f"{md}\n\n--- END OF DECK ---"
        ),
    }
