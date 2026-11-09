"""The no-new-facts guard (T10, T21): what it blocks, what it allows, and what it only notes."""

import pytest

from talentsift.draft_template import FactView, template_draft
from talentsift.guard import check_draft, facts_hash
from talentsift.schemas import DraftExperience, ResumeDraft

FACTS = [
    FactView(1, "skills", "SQL, Tableau, Excel"),
    FactView(2, "experience", "Data Analyst", "Fictional Grocers", "2021", "2025",
             "Built 12 Tableau dashboards for the finance team\nWrote SQL queries to analyze weekly sales\nTrained 3 new analysts"),
    FactView(3, "experience", "Analytics Intern", "Mock Markets", "2020", "2021", "Cleaned vendor files in Excel"),
    FactView(4, "education", "B.S. Business Analytics", "Example State University", "", "2019"),
    FactView(5, "certifications", "Tableau Desktop Specialist", "Tableau", "", "2022"),
]
POSTING = "Requirements:\n- SQL and Tableau dashboards\n- Snowflake and dbt pipelines\n- Stakeholder communication\n- Forecasting"


def draft(summary="", skills=(), bullets=None, education=(4,), certifications=(5,), fact_id=2):
    experience = [DraftExperience(fact_id=fact_id, bullets=list(bullets))] if bullets else []
    return ResumeDraft(summary=summary, skills=list(skills), experience=experience,
                       education_ids=list(education), certification_ids=list(certifications))


def kinds(report):
    return {(v.kind, v.text) for v in report.violations}


def test_a_faithful_rewording_passes():
    report = check_draft(
        draft(
            summary="Data analyst with experience building Tableau dashboards and writing SQL queries.",
            skills=["SQL", "Tableau", "Excel"],
            bullets=["Built 12 Tableau dashboards for the finance team.", "Analyzed weekly sales using SQL queries.", "Trained 3 new analysts."],
        ),
        FACTS, POSTING,
    )
    assert report.passed, report.violations
    assert report.lines_checked == 7 and report.facts_hash == facts_hash(FACTS)


@pytest.mark.parametrize("role_text", ["", POSTING, "Tableau SQL Excel analyst dashboards finance sales"])
@pytest.mark.parametrize("headline", ["", "Business data analyst"])
def test_the_fact_template_always_passes(role_text, headline):
    result = template_draft(FACTS, role_text, headline)
    report = check_draft(result, FACTS, role_text, headline)
    assert report.passed and not report.notes, report.violations
    assert [e.fact_id for e in result.experience] == [2, 3] and result.education_ids == [4]


def test_template_puts_the_most_relevant_facts_first():
    result = template_draft(FACTS, "Excel vendor files")
    assert result.skills[0] == "Excel" and result.experience[0].fact_id == 3


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"skills": ["SQL", "Snowflake"]}, ("skill", "Snowflake")),
        ({"skills": ["SQ"]}, ("skill", "SQ")),  # must match whole words, not fragments
        ({"bullets": ["Built 40 Tableau dashboards for the finance team"]}, ("number", "40")),
        ({"bullets": ["Cut reporting time by 30%"]}, ("number", "30")),
        ({"bullets": ["Trained five new analysts"]}, ("number", "five")),
        ({"bullets": ["Built Tableau dashboards on Snowflake"]}, ("named_term", "Snowflake")),
        ({"bullets": ["Built dashboards in Power BI for finance"]}, ("named_term", "BI")),
        ({"bullets": ["Built dbt pipelines for weekly sales"]}, ("posting_term", "dbt")),
        ({"summary": "Analyst skilled in forecasting and stakeholder communication."}, ("posting_term", "forecasting")),
        ({"summary": "Certified data analyst."}, ("credential", "Certified")),
        ({"summary": "Senior data analyst with a master's degree."}, ("credential", "Senior")),
        ({"bullets": ["Promoted to lead the finance team"]}, ("credential", "Promoted")),
        ({"summary": "Analyst since 2015."}, ("number", "2015")),
    ],
)
def test_anything_beyond_the_facts_is_blocked(kwargs, expected):
    report = check_draft(draft(**kwargs), FACTS, POSTING)
    assert not report.passed
    assert expected in kinds(report), report.violations


def test_a_number_must_come_from_the_same_job_not_another_one():
    # "12" is a real number, but it belongs to the Data Analyst job, not the internship.
    report = check_draft(draft(bullets=["Cleaned 12 vendor files in Excel"], fact_id=3), FACTS)
    assert ("number", "12") in kinds(report)
    assert check_draft(draft(bullets=["Cleaned vendor files in Excel"], fact_id=3), FACTS).passed


def test_fact_ids_must_exist_and_sit_in_the_right_section():
    assert ("unknown_fact", "99") in kinds(check_draft(draft(bullets=["Built dashboards"], fact_id=99), FACTS))
    assert ("unknown_fact", "5") in kinds(check_draft(draft(education=(5,), certifications=()), FACTS))  # a cert listed as a degree
    assert ("unknown_fact", "4") in kinds(check_draft(draft(bullets=["Studied analytics"], fact_id=4), FACTS))
    assert not check_draft(draft(education=(4, 4)), FACTS).passed
    assert not check_draft(draft(education=(), certifications=()), FACTS).passed  # an empty draft is not a resume


def test_credential_words_need_an_exact_match_not_a_similar_word():
    facts = [*FACTS, FactView(6, "education", "Certificate coursework in statistics", "Example College")]
    assert ("credential", "Certified") in kinds(check_draft(draft(summary="Certified in statistics."), facts))
    assert check_draft(draft(summary="Completed certificate coursework in statistics."), facts).passed


def test_unfamiliar_but_harmless_wording_is_noted_not_blocked():
    report = check_draft(draft(bullets=["Carefully wrote SQL queries to analyze weekly sales"]), FACTS, POSTING)
    assert report.passed
    assert any("Carefully" in note for note in report.notes)


def test_ordinary_words_match_by_stem_but_named_terms_must_match_exactly():
    assert check_draft(draft(bullets=["Analysis of weekly sales with SQL; training for new analysts"]), FACTS).passed
    assert ("named_term", "MySQL") in kinds(check_draft(draft(bullets=["Wrote MySQL queries"]), FACTS))


def test_facts_hash_changes_when_any_fact_changes():
    changed = [*FACTS[:-1], FactView(5, "certifications", "Tableau Desktop Specialist", "Tableau", "", "2023")]
    assert facts_hash(FACTS) != facts_hash(changed)
    assert facts_hash(FACTS) != facts_hash(FACTS, "Analyst")
