"""Resume ingestion: file -> parse -> mask -> Applicant row, with audit events.

Duplicates (same file bytes) are skipped. Files that need OCR or have no parser are still recorded so the
manager can see why they were skipped; screening only picks up `parsed` and `partial` resumes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from talentsift.audit import log_event, sha256_text
from talentsift.config import Settings
from talentsift.masking import MaskResult, mask_text
from talentsift.models import (
    PARSE_ERROR,
    PARSE_NEEDS_OCR,
    PARSE_UNSUPPORTED,
    SCREENABLE_PARSE_STATUSES,
    Applicant,
    dumps,
)
from talentsift.parsers import parse_file, supported_extensions

ADDED = "added"
DUPLICATE = "duplicate"


@dataclass
class IngestResult:
    """Outcome for one file, ready to show in the UI."""

    filename: str
    outcome: str  # "added" or "duplicate"
    parse_status: str
    message: str
    applicant: Applicant | None = None

    @property
    def screenable(self) -> bool:
        return self.applicant is not None and self.parse_status in SCREENABLE_PARSE_STATUSES


def default_batch_name(prefix: str = "Upload") -> str:
    return f"{prefix} {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"


def label_for(applicant_id: int) -> str:
    """Display label shown to the manager and the model, e.g. "Applicant 07"."""
    return f"Applicant {applicant_id:02d}"


def ingest_file(
    session: Session,
    path: Path,
    *,
    batch: str,
    settings: Settings,
    original_filename: str | None = None,
) -> IngestResult:
    """Ingest one file from disk and commit."""
    path = Path(path)
    filename = original_filename or path.name
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    existing = session.exec(select(Applicant).where(Applicant.file_hash == file_hash)).first()
    if existing is not None:
        return IngestResult(
            filename=filename,
            outcome=DUPLICATE,
            parse_status=existing.parse_status,
            message=f"Skipped: identical file already ingested as {existing.display_label}.",
            applicant=existing,
        )

    parsed, parser_name = parse_file(path)
    masked = mask_text(parsed.text, mask_grad_years=settings.mask_grad_years) if parsed.text else MaskResult("", {})

    applicant = Applicant(
        original_filename=filename,
        file_hash=file_hash,
        batch=batch,
        parse_status=parsed.status,
        parse_message=parsed.message,
        page_count=parsed.page_count,
        raw_text=parsed.text,
        masked_text=masked.masked_text,
        mask_counts_json=dumps(masked.counts),
    )
    session.add(applicant)
    session.flush()  # assigns the id used for the display label
    applicant.display_label = label_for(applicant.id)

    # Audit payloads carry labels, hashes, and counts only: no filenames or text (they may contain PII).
    log_event(
        session,
        "resume_ingested",
        applicant_id=applicant.id,
        input_hash=file_hash,
        payload={
            "display_label": applicant.display_label,
            "batch": batch,
            "parser": parser_name,
            "parse_status": parsed.status,
            "parse_message": parsed.message,
            "page_count": parsed.page_count,
            "image_count": parsed.image_count,
            "scanned_pages": parsed.scanned_pages,
            "text_chars": len(parsed.text),
        },
    )
    if parsed.text:
        log_event(
            session,
            "resume_masked",
            applicant_id=applicant.id,
            input_hash=sha256_text(masked.masked_text),
            payload={
                "display_label": applicant.display_label,
                "counts": masked.counts,
                "mask_grad_years": settings.mask_grad_years,
                "masked_chars": len(masked.masked_text),
            },
        )
    session.commit()

    message = parsed.message or f"Parsed {parsed.page_count} page(s), {len(parsed.text):,} characters."
    return IngestResult(
        filename=filename, outcome=ADDED, parse_status=parsed.status, message=message, applicant=applicant
    )


def ingest_upload(
    session: Session,
    filename: str,
    data: bytes,
    *,
    batch: str,
    settings: Settings,
) -> IngestResult:
    """Save uploaded bytes under `settings.upload_dir` (named by hash) and ingest that copy."""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    stored = settings.upload_dir / f"{digest[:16]}{Path(filename).suffix.lower()}"
    if not stored.exists():
        stored.write_bytes(data)
    return ingest_file(session, stored, batch=batch, settings=settings, original_filename=Path(filename).name)


def ingest_folder(
    session: Session,
    folder: Path,
    *,
    settings: Settings,
    batch: str | None = None,
) -> list[IngestResult]:
    """Import every supported file in a folder (not recursive), in filename order."""
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    batch = batch or folder.name
    extensions = supported_extensions()
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in extensions)
    return [ingest_file(session, path, batch=batch, settings=settings) for path in files]


def list_batches(session: Session) -> list[str]:
    rows = session.exec(select(Applicant.batch).distinct().order_by(Applicant.batch))
    return [row for row in rows]


def remask_applicants(session: Session, settings: Settings) -> int:
    """Re-apply masking with the current settings (e.g. after turning MASK_GRAD_YEARS on).

    Returns how many applicants changed. Each change is logged; cached results no longer match the new
    masked text, so the next screening run calls the model again for those applicants.
    """
    changed = 0
    for applicant in session.exec(select(Applicant).order_by(Applicant.id)):
        if not applicant.raw_text:
            continue
        masked = mask_text(applicant.raw_text, mask_grad_years=settings.mask_grad_years)
        if masked.masked_text == applicant.masked_text:
            continue
        applicant.masked_text = masked.masked_text
        applicant.mask_counts_json = dumps(masked.counts)
        log_event(
            session,
            "resume_masked",
            applicant_id=applicant.id,
            input_hash=sha256_text(masked.masked_text),
            payload={
                "display_label": applicant.display_label,
                "counts": masked.counts,
                "mask_grad_years": settings.mask_grad_years,
                "masked_chars": len(masked.masked_text),
                "reason": "re-masked with current settings",
            },
        )
        changed += 1
    session.commit()
    return changed


SKIP_MESSAGES = {
    PARSE_NEEDS_OCR: "needs OCR (scanned image); skipped",
    PARSE_UNSUPPORTED: "file type not supported yet; skipped",
    PARSE_ERROR: "could not be read; skipped",
}
