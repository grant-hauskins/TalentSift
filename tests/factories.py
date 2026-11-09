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


# --- Candidate side ----------------------------------------------------------------------------------

ANALYST_POSTING = """Business Data Analyst
Requirements:
- SQL queries and Tableau dashboards
- Snowflake and dbt experience
- Stakeholder communication
- Bachelor's degree
"""


def make_candidate(session, *, with_facts: bool = True):
    """A fictional candidate with one fact in each category. Returns (profile, access code)."""
    from talentsift.candidates import add_fact, create_profile

    profile, code = create_profile(
        session, full_name="Avery Exampleton", consent=True, email="avery@example.com", phone="(312) 555-0101",
        location="Chicago, IL", headline="Business data analyst",
    )
    if with_facts:
        add_fact(session, profile, category="skills", title="SQL, Tableau, Excel, Python")
        add_fact(
            session, profile, category="experience", title="Data Analyst", organization="Fictional Grocers",
            start="2021", end="2025",
            details="Built 12 Tableau dashboards for the finance team\nWrote SQL queries to analyze weekly sales\nTrained 3 new analysts",
        )
        add_fact(session, profile, category="experience", title="Analytics Intern", organization="Mock Markets", start="2020", end="2021",
                 details="Cleaned vendor files in Excel")
        add_fact(session, profile, category="education", title="B.S. Business Analytics", organization="Example State University", end="2019")
        add_fact(session, profile, category="certifications", title="Tableau Desktop Specialist", organization="Tableau", end="2022")
    return profile, code
