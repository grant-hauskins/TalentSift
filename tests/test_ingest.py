"""Ingestion: upload and folder import, duplicates, OCR skips, and audit events."""

import json

import pytest
from sqlmodel import select

from talentsift.audit import list_events
from talentsift.ingest import (
    DUPLICATE,
    already_ingested,
    ingest_folder,
    ingest_upload,
    is_pdf,
    list_batches,
    remask_applicants,
    scan_folder,
)
from talentsift.models import PARSE_NEEDS_OCR, PARSE_PARSED, Applicant

from tests.pdf_factory import RESUME_LINES, scanned_pdf, text_pdf


@pytest.fixture
def folder(tmp_path):
    folder = tmp_path / "batch-a"
    folder.mkdir()
    text_pdf(folder / "01_casey.pdf", [RESUME_LINES])
    text_pdf(folder / "02_other.pdf", [["Robin Exampleton", "", "SKILLS", "Excel, scheduling, vendor management"] * 5])
    scanned_pdf(folder / "03_scan.pdf", RESUME_LINES)
    (folder / "04_resume.docx").write_bytes(b"PK fake")
    (folder / "notes.txt").write_text("ignored: no parser for .txt")
    return folder


def test_folder_import_reads_only_pdfs(session, settings, folder):
    results = ingest_folder(session, folder, settings=settings)
    assert [r.filename for r in results] == ["01_casey.pdf", "02_other.pdf", "03_scan.pdf"]
    statuses = [r.parse_status for r in results]
    assert statuses == [PARSE_PARSED, PARSE_PARSED, PARSE_NEEDS_OCR]
    assert [r.screenable for r in results] == [True, True, False]
    assert "OCR" in results[2].message
    assert list_batches(session) == ["batch-a"]


def test_applicant_stores_filename_hash_text_and_masked_text(session, settings, folder):
    ingest_folder(session, folder, settings=settings)
    applicant = session.exec(select(Applicant).where(Applicant.original_filename == "01_casey.pdf")).one()
    assert applicant.display_label == f"Applicant {applicant.id:02d}"
    assert len(applicant.file_hash) == 64
    assert "Casey Q. Sampleton" in applicant.raw_text
    assert "Sampleton" not in applicant.masked_text and "[NAME]" in applicant.masked_text
    assert json.loads(applicant.mask_counts_json)["emails"] == 1


def test_duplicates_are_skipped(session, settings, folder):
    ingest_folder(session, folder, settings=settings)
    again = ingest_folder(session, folder, settings=settings)
    assert all(result.outcome == DUPLICATE for result in again)
    assert len(session.exec(select(Applicant)).all()) == 3


def test_upload_is_saved_and_ingested(session, settings, tmp_path):
    settings = settings.with_overrides(upload_dir=tmp_path / "uploads")
    pdf = text_pdf(tmp_path / "Casey_Sampleton_Resume.pdf", [RESUME_LINES])
    result = ingest_upload(session, "Casey_Sampleton_Resume.pdf", pdf.read_bytes(), batch="Upload 1", settings=settings)
    assert result.outcome == "added" and result.parse_status == PARSE_PARSED
    assert result.applicant.original_filename == "Casey_Sampleton_Resume.pdf"
    assert len(list((tmp_path / "uploads").iterdir())) == 1


def test_ingest_writes_audit_events_without_pii(session, settings, folder):
    ingest_folder(session, folder, settings=settings)
    events = list_events(session, event_types=["resume_ingested", "resume_masked"])
    assert [e.event_type for e in events].count("resume_ingested") == 3
    # The scanned PDF produces no text, so only the two text PDFs are masked.
    assert [e.event_type for e in events].count("resume_masked") == 2
    for event in events:
        assert "casey" not in event.payload_json.lower()
        assert "01_casey.pdf" not in event.payload_json
        assert event.input_hash


def test_missing_folder_raises(session, settings, tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest_folder(session, tmp_path / "nope", settings=settings)


def test_remask_applies_grad_year_flag(session, settings, folder):
    ingest_folder(session, folder, settings=settings)
    changed = remask_applicants(session, settings.with_overrides(mask_grad_years=True))
    assert changed == 1  # only Casey's resume has an education section with a year
    applicant = session.exec(select(Applicant).where(Applicant.original_filename == "01_casey.pdf")).one()
    assert "Graduated [YEAR]" in applicant.masked_text


def test_scan_folder_explains_what_will_and_will_not_be_sent(folder):
    (folder / "05_renamed.pdf").write_bytes(b"PK this is really a Word file")
    (folder / "06_empty.pdf").write_bytes(b"")
    (folder / "._01_casey.pdf").write_bytes(b"%PDF-1.4 macOS resource fork")
    (folder / "subfolder").mkdir()
    text_pdf(folder / "subfolder" / "nested.pdf", [RESUME_LINES])

    scan = scan_folder(folder)
    assert [f.name for f in scan.included] == ["01_casey.pdf", "02_other.pdf", "03_scan.pdf"]
    reasons = {f.name: f.reason for f in scan.excluded}
    assert reasons == {
        "._01_casey.pdf": "hidden or system file",
        "04_resume.docx": "not a PDF (.docx)",
        "05_renamed.pdf": "named .pdf but the contents are not a PDF",
        "06_empty.pdf": "empty file",
        "notes.txt": "not a PDF (.txt)",
    }
    assert all(f.size_bytes > 0 for f in scan.included)


def test_renamed_non_pdf_is_never_ingested(session, settings, folder):
    (folder / "05_renamed.pdf").write_bytes(b"PK this is really a Word file")
    results = ingest_folder(session, folder, settings=settings)
    assert "05_renamed.pdf" not in [r.filename for r in results]


def test_is_pdf_checks_extension_and_contents(tmp_path):
    good = text_pdf(tmp_path / "a.PDF", [RESUME_LINES])
    fake = tmp_path / "b.pdf"
    fake.write_text("hello")
    other = tmp_path / "c.txt"
    other.write_bytes(good.read_bytes())
    assert is_pdf(good) and not is_pdf(fake) and not is_pdf(other)


def test_already_ingested_maps_duplicates_to_labels(session, settings, folder):
    ingest_folder(session, folder, settings=settings)
    text_pdf(folder / "07_new.pdf", [["Brand new resume text", "SKILLS", "Forklift, inventory"] * 5])
    found = already_ingested(session, [f.path for f in scan_folder(folder).included])
    assert sorted(p.name for p in found) == ["01_casey.pdf", "02_other.pdf", "03_scan.pdf"]
    assert all(label.startswith("Applicant ") for label in found.values())
