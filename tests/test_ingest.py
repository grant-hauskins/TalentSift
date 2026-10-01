"""Ingestion: upload and folder import, duplicates, OCR skips, and audit events."""

import json

import pytest
from sqlmodel import select

from talentsift.audit import list_events
from talentsift.ingest import DUPLICATE, ingest_folder, ingest_upload, list_batches, remask_applicants
from talentsift.models import PARSE_NEEDS_OCR, PARSE_PARSED, PARSE_UNSUPPORTED, Applicant

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


def test_folder_import_records_every_supported_file(session, settings, folder):
    results = ingest_folder(session, folder, settings=settings)
    assert [r.filename for r in results] == ["01_casey.pdf", "02_other.pdf", "03_scan.pdf", "04_resume.docx"]
    statuses = [r.parse_status for r in results]
    assert statuses == [PARSE_PARSED, PARSE_PARSED, PARSE_NEEDS_OCR, PARSE_UNSUPPORTED]
    assert [r.screenable for r in results] == [True, True, False, False]
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
    assert len(session.exec(select(Applicant)).all()) == 4


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
    assert [e.event_type for e in events].count("resume_ingested") == 4
    # The scanned PDF and the DOCX stub produce no text, so only the two text PDFs are masked.
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
