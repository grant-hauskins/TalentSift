"""PDF parsing, OCR detection, and the parser registry."""

from pathlib import Path

from talentsift.models import PARSE_ERROR, PARSE_NEEDS_OCR, PARSE_PARSED, PARSE_PARTIAL, PARSE_UNSUPPORTED
from talentsift.parsers import get_parser, parse_file, register_parser, supported_extensions
from talentsift.parsers.base import ParsedResume, ResumeParser
from talentsift.parsers.pdf import PdfParser

from tests.pdf_factory import RESUME_LINES, mixed_pdf, scanned_pdf, text_pdf

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "resumes" / "samples"


def test_pdf_parser_extracts_text(tmp_path):
    path = text_pdf(tmp_path / "resume.pdf", [RESUME_LINES])
    parsed = PdfParser().parse(path)
    assert parsed.status == PARSE_PARSED
    assert parsed.page_count == 1
    assert "Casey Q. Sampleton" in parsed.text
    assert "Built Tableau dashboards used by regional managers every Monday." in parsed.text


def test_committed_sample_resumes_parse():
    pdfs = sorted(SAMPLES.glob("*.pdf"))
    if not pdfs:  # samples are generated in build step 6; this test tightens automatically then
        return
    statuses = {path.name: parse_file(path)[0].status for path in pdfs}
    assert sum(status == PARSE_PARSED for status in statuses.values()) >= 15


def test_near_empty_pdf_is_flagged_needs_ocr(tmp_path):
    path = scanned_pdf(tmp_path / "scan.pdf", RESUME_LINES)
    parsed = PdfParser().parse(path)
    assert parsed.status == PARSE_NEEDS_OCR
    assert "OCR" in parsed.message
    assert parsed.image_count == 1


def test_pdf_with_a_scanned_page_is_partial(tmp_path):
    path = mixed_pdf(tmp_path / "mixed.pdf", RESUME_LINES, ["Second page scanned", "More experience"])
    parsed = PdfParser().parse(path)
    assert parsed.status == PARSE_PARTIAL
    assert parsed.scanned_pages == [2]
    assert "Needs review" in parsed.message


def test_photo_is_dropped_from_text(tmp_path):
    path = text_pdf(tmp_path / "photo.pdf", [RESUME_LINES], photo=True)
    parsed = PdfParser().parse(path)
    assert parsed.status == PARSE_PARSED  # a photo on a page with text is not a scanned page
    assert parsed.image_count == 1
    assert "base64" not in parsed.text and "data:image" not in parsed.text


def test_damaged_pdf_reports_error(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4 this is not really a pdf")
    parsed, _ = parse_file(path)
    assert parsed.status == PARSE_ERROR
    assert parsed.text == ""


def test_docx_stub_reports_planned(tmp_path):
    path = tmp_path / "resume.docx"
    path.write_bytes(b"PK fake docx")
    parsed, parser_name = parse_file(path)
    assert parser_name == "docx"
    assert parsed.status == PARSE_UNSUPPORTED
    assert "planned for final project" in parsed.message


def test_unknown_extension_is_unsupported(tmp_path):
    path = tmp_path / "resume.rtf"
    path.write_text("hello")
    parsed, parser_name = parse_file(path)
    assert parser_name == "none"
    assert parsed.status == PARSE_UNSUPPORTED


def test_registry_picks_parser_by_extension():
    assert get_parser(Path("x.PDF")).name == "pdf"
    assert get_parser(Path("x.docx")).name == "docx"
    assert get_parser(Path("x.txt")) is None
    assert ".pdf" in supported_extensions()


def test_new_parser_plugs_in_without_touching_scoring(tmp_path):
    class TxtParser:
        name = "txt"
        extensions = (".txt",)

        def supports(self, path):
            return Path(path).suffix.lower() == ".txt"

        def parse(self, path):
            return ParsedResume(text=Path(path).read_text(), status=PARSE_PARSED, page_count=1)

    parser = TxtParser()
    assert isinstance(parser, ResumeParser)
    register_parser(parser)
    try:
        path = tmp_path / "resume.txt"
        path.write_text("Plain text resume")
        parsed, name = parse_file(path)
        assert name == "txt" and parsed.text == "Plain text resume"
    finally:
        from talentsift.parsers import registry

        registry._PARSERS.remove(parser)
