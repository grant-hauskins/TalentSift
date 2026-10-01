"""Tiny PDF builders for tests (reportlab). Each returns the path it wrote."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def _photo(width: int = 120, height: int = 150) -> ImageReader:
    image = Image.new("RGB", (width, height), (200, 170, 140))
    draw = ImageDraw.Draw(image)
    draw.ellipse((30, 20, 90, 80), fill=(120, 90, 70))
    return ImageReader(image)


def _picture_of_text(lines: list[str]) -> ImageReader:
    image = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((20, 20 + index * 24), line, fill="black")
    return ImageReader(image)


def text_pdf(path: Path, pages: list[list[str]], *, photo: bool = False) -> Path:
    """A normal text PDF. `pages` is a list of pages, each a list of lines."""
    pdf = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    for page_number, lines in enumerate(pages):
        if photo and page_number == 0:
            pdf.drawImage(_photo(), 450, 620, width=100, height=125)
        y = 740
        for line in lines:
            pdf.drawString(60, y, line)
            y -= 16
        pdf.showPage()
    pdf.save()
    return path


def scanned_pdf(path: Path, lines: list[str]) -> Path:
    """An image-only PDF: the text is a picture, so nothing is selectable."""
    pdf = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    pdf.drawImage(_picture_of_text(lines), 40, 300, width=530, height=353)
    pdf.showPage()
    pdf.save()
    return path


def mixed_pdf(path: Path, text_lines: list[str], scanned_lines: list[str]) -> Path:
    """Page 1 has real text; page 2 is a scanned image."""
    pdf = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    y = 740
    for line in text_lines:
        pdf.drawString(60, y, line)
        y -= 16
    pdf.showPage()
    pdf.drawImage(_picture_of_text(scanned_lines), 40, 300, width=530, height=353)
    pdf.showPage()
    pdf.save()
    return path


RESUME_LINES = [
    "Casey Q. Sampleton",
    "casey.sampleton@example.com | (614) 555-0142 | linkedin.com/in/caseysampleton",
    "42 Example Street, Anytown, OH 43004",
    "",
    "SUMMARY",
    "Data analyst who builds SQL reports and Tableau dashboards for operations leaders.",
    "",
    "EXPERIENCE",
    "Data Analyst, Fictional Foods Inc., 2020 - 2024",
    "- Wrote SQL queries joining 12 tables to track weekly sales for 40 stores.",
    "- Built Tableau dashboards used by regional managers every Monday.",
    "- Casey presented findings to the VP of Operations each quarter.",
    "",
    "EDUCATION",
    "B.S. Statistics, Example State University",
    "Graduated 2019",
]
