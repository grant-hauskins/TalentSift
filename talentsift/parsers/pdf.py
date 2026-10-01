"""Text-based PDF parser (pdfplumber).

Images are never extracted: `extract_text()` only returns text objects, so photos cannot leak into the
prompt. Pages with images but almost no text are reported as scanned pages.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from talentsift.models import PARSE_ERROR, PARSE_NEEDS_OCR, PARSE_PARSED, PARSE_PARTIAL
from talentsift.parsers.base import ParsedResume, normalize_extracted_text

# A resume with less selectable text than this is treated as an image-only (scanned) PDF.
MIN_TOTAL_CHARS = 200
# A page with images and less text than this looks like a scanned page.
MIN_PAGE_CHARS = 30


class PdfParser:
    name = "pdf"
    extensions = (".pdf",)

    def supports(self, path: Path) -> bool:
        return Path(path).suffix.lower() in self.extensions

    def parse(self, path: Path) -> ParsedResume:
        page_texts: list[str] = []
        scanned_pages: list[int] = []
        image_count = 0
        try:
            with pdfplumber.open(path) as pdf:
                for number, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    page_images = len(page.images)
                    image_count += page_images
                    if page_images and len(text.strip()) < MIN_PAGE_CHARS:
                        scanned_pages.append(number)
                    page_texts.append(text)
        except Exception as exc:  # pdfplumber raises many error types for damaged files
            return ParsedResume(
                text="",
                status=PARSE_ERROR,
                message=f"Could not read this PDF ({type(exc).__name__}). Check that the file opens and is not encrypted.",
            )

        text = normalize_extracted_text("\n\n".join(page_texts))
        page_count = len(page_texts)
        common = dict(page_count=page_count, image_count=image_count, scanned_pages=scanned_pages)

        if len(text) < MIN_TOTAL_CHARS:
            return ParsedResume(
                text=text,
                status=PARSE_NEEDS_OCR,
                message=(
                    f"Only {len(text)} characters of selectable text were found, so this looks like a scanned "
                    "image. It was skipped. OCR support is planned for the final project; until then, ask for a "
                    "text-based PDF."
                ),
                **common,
            )
        if scanned_pages:
            pages = ", ".join(str(n) for n in scanned_pages)
            return ParsedResume(
                text=text,
                status=PARSE_PARTIAL,
                message=(
                    f"Page(s) {pages} contain images but no selectable text, so part of this resume could not be "
                    "read. It will be screened, but it can never be auto-rejected; it goes to Needs review."
                ),
                **common,
            )
        return ParsedResume(text=text, status=PARSE_PARSED, **common)
