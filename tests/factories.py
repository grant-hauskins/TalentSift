"""Builders for roles and applicants used across tests (no PDFs needed)."""

from __future__ import annotations

import hashlib

from talentsift.ingest import label_for
from talentsift.masking import mask_text
from talentsift.models import PARSE_PARSED, Applicant
from talentsift.roles import CriterionInput, approve_role, create_role

ANALYST_CRITERIA = [
    CriterionInput("SQL querying", "Writes SQL queries to analyze data", "must_have", "high"),
    CriterionInput("Dashboards", "Builds dashboards in Tableau or Power BI", "must_have", "high"),
    CriterionInput("Stakeholder communication", "Presents findings to stakeholders", "must_have", "medium"),
    CriterionInput("Python", "Uses Python pandas for data cleaning", "nice_to_have", "medium"),
    CriterionInput("Experimentation", "Runs A/B testing with statistics", "nice_to_have", "low"),
]

STRONG = """Avery Strongfit | avery@example.com | (614) 555-0101
SUMMARY
Analyst who writes SQL queries to analyze data every day.
EXPERIENCE
- Built dashboards in Tableau and Power BI for the finance team.
- Presented findings to stakeholders in monthly business reviews.
- Used Python pandas for data cleaning of messy vendor files.
- Ran A/B testing with statistics to measure promotions.
"""

MEDIUM = """Blake Middleton | blake@example.com
SUMMARY
Operations associate.
EXPERIENCE
- Wrote SQL queries to analyze data for weekly inventory counts.
- Kept a simple dashboard in Tableau for store leads.
- Presented findings to stakeholders at the store managers meeting.
- Kept spreadsheets of store budgets.
"""

WEAK = """Casey Unrelated | casey@example.com
SUMMARY
Friendly retail associate.
EXPERIENCE
- Greeted customers and handled returns at the register.
- Restocked shelves and organized the stockroom.
"""


def make_applicant(session, text: str, *, batch: str = "test", parse_status: str = PARSE_PARSED, parse_message: str = "") -> Applicant:
    applicant = Applicant(
        original_filename=f"{(text.splitlines() or ['scanned'])[0][:20]}.pdf",
        file_hash=hashlib.sha256(f"{text}|{parse_status}|{batch}".encode()).hexdigest(),
        batch=batch,
        parse_status=parse_status,
        parse_message=parse_message,
        page_count=1,
        raw_text=text,
        masked_text=mask_text(text).masked_text if text else "",
    )
    session.add(applicant)
    session.flush()
    applicant.display_label = label_for(applicant.id)
    session.commit()
    return applicant


def make_role(session, *, criteria=None, approve: bool = True, threshold: int = 40, top_n: int = 5, title: str = "Business Data Analyst"):
    criteria = [CriterionInput(**c.__dict__) for c in (criteria or ANALYST_CRITERIA)]  # fresh copies
    role = create_role(
        session,
        title=title,
        summary="Turns sales data into decisions.",
        criteria=criteria,
        auto_reject_threshold=threshold,
        top_n=top_n,
    )
    if approve:
        approve_role(session, role)
    return role
