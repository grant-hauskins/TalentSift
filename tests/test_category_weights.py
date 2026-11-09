"""Employer category weights: the score math, the breakdown, versioning, and repeatability (T18)."""

import pytest
from sqlmodel import select

from talentsift.audit import event_payload, list_events
from talentsift.llm.fake_client import FakeLLMClient
from talentsift.models import CATEGORIES, Evaluation, loads
from talentsift.roles import CriterionInput, RoleError, approve_role, current_criteria, discard_draft, duplicate_role, save_role
from talentsift.scoring import CriterionResult, compute_fit_score, run_screening

from tests.factories import MEDIUM, STRONG, WEAK, make_applicant, make_role

MIXED = [
    CriterionInput("SQL querying", "Writes SQL queries to analyze data", "must_have", "high", category="skills"),
    CriterionInput("Dashboards", "Builds dashboards in Tableau or Power BI", "nice_to_have", "medium", category="skills"),
    CriterionInput("Stakeholder communication", "Presents findings to stakeholders", "nice_to_have", "medium", category="experience"),
    CriterionInput("Statistics degree", "Degree in statistics", "nice_to_have", "low", category="education"),
]


def result(cid, weight, score, category, type_="nice_to_have"):
    return CriterionResult(cid, type_, weight, score, category)


def test_fit_score_combines_category_scores_by_the_employers_weights():
    results = [
        result(1, "high", 4, "skills"),  # skills: (3*4 + 1*2) / (4*4) = 87.5
        result(2, "low", 2, "skills"),
        result(3, "medium", 2, "experience"),  # experience: 50.0
        result(4, "medium", 0, "education"),  # education: 0.0
    ]
    fit = compute_fit_score(results, 15, {"skills": 50, "experience": 30, "education": 20, "certifications": 40})
    # Certifications has no criteria, so its 40 is ignored: shares are 50/100, 30/100, 20/100.
    assert fit.raw_score == pytest.approx(0.5 * 87.5 + 0.3 * 50.0 + 0.2 * 0.0, abs=0.05)  # 58.75
    assert fit.fit_score == 58.8 and fit.penalty_points == 0
    by_category = {c.category: c for c in fit.categories}
    assert [c.category for c in fit.categories] == list(CATEGORIES)
    assert by_category["skills"].score == 87.5 and by_category["skills"].share == 0.5
    assert by_category["certifications"].score is None and by_category["certifications"].points == 0
    assert sum(c.points for c in fit.categories) == pytest.approx(58.75)


def test_weights_change_the_score_and_only_relative_size_matters():
    results = [result(1, "high", 4, "skills"), result(2, "high", 0, "education")]
    assert compute_fit_score(results, 0, {"skills": 50, "education": 50}).fit_score == 50.0
    assert compute_fit_score(results, 0, {"skills": 90, "education": 10}).fit_score == 90.0
    assert compute_fit_score(results, 0, {"skills": 9, "education": 1}).fit_score == 90.0
    assert compute_fit_score(results, 0, {"skills": 100, "education": 0}).fit_score == 100.0


def test_one_category_gives_the_original_weighted_average():
    # The prototype's hand-calculated example: 20 of 24 points -> 83.3, whatever the weights say.
    results = [result(1, "high", 4, "skills"), result(2, "medium", 3, "skills"), result(3, "low", 2, "skills")]
    assert compute_fit_score(results, 15).fit_score == 83.3
    assert compute_fit_score(results, 15, {"skills": 7, "education": 93}).fit_score == 83.3


def test_must_have_penalty_applies_after_the_category_weights():
    results = [result(1, "high", 1, "skills", "must_have"), result(2, "high", 4, "experience")]
    fit = compute_fit_score(results, 15, {"skills": 50, "experience": 50})
    assert fit.raw_score == 62.5 and fit.penalty_points == 15 and fit.fit_score == 47.5
    assert fit.must_haves_met == 0 and fit.unmet_must_have_ids == (1,)


def test_all_used_categories_weighted_zero_falls_back_to_equal_shares():
    results = [result(1, "high", 4, "skills"), result(2, "high", 0, "education")]
    assert compute_fit_score(results, 0, {"skills": 0, "education": 0, "experience": 80}).fit_score == 50.0


def test_role_stores_weights_and_rejects_bad_ones(session):
    role = make_role(session, criteria=MIXED, approve=False)
    assert role.category_weights == {c: 25 for c in CATEGORIES}

    def save(weights):
        return save_role(
            session, role, title=role.title, summary=role.summary, posting_text=role.posting_text,
            criteria=[CriterionInput.from_criterion(c) for c in current_criteria(session, role)],
            auto_reject_threshold=role.auto_reject_threshold, top_n=role.top_n, category_weights=weights,
        )

    assert save({"skills": 60, "experience": 30, "education": 10, "certifications": 0}).action == "draft_updated"
    assert role.category_weights["skills"] == 60
    with pytest.raises(RoleError, match="between 0 and 100"):
        save({"skills": 101})
    with pytest.raises(RoleError, match="at least one category"):
        save({"skills": 0, "experience": 0, "education": 0, "certifications": 50})  # no criteria use certifications
    with pytest.raises(RoleError, match="Unknown category"):
        save({"charisma": 10})


def test_changing_weights_on_an_approved_rubric_creates_a_new_version_and_discard_restores_them(session):
    role = make_role(session, criteria=MIXED)
    before = role.category_weights
    outcome = save_role(
        session, role, title=role.title, summary=role.summary, posting_text=role.posting_text,
        criteria=[CriterionInput.from_criterion(c) for c in current_criteria(session, role)],
        auto_reject_threshold=role.auto_reject_threshold, top_n=role.top_n,
        category_weights={"skills": 80, "experience": 10, "education": 10, "certifications": 0},
    )
    assert outcome.action == "new_version" and role.version == 2 and not role.is_approved
    assert [c.category for c in current_criteria(session, role)] == ["skills", "skills", "experience", "education"]
    discard_draft(session, role)
    assert role.version == 1 and role.is_approved and role.category_weights == before


def test_duplicate_keeps_categories_and_weights(session):
    role = make_role(session, criteria=MIXED, approve=False)
    role.category_weights_json = '{"skills": 70, "experience": 20, "education": 10, "certifications": 0}'
    session.commit()
    copy = duplicate_role(session, role)
    assert copy.category_weights == role.category_weights
    assert [c.category for c in current_criteria(session, copy)] == [c.category for c in current_criteria(session, role)]


def screen(session, settings, role, applicants):
    run = run_screening(session, role=role, applicants=applicants, client=FakeLLMClient(), settings=settings)
    rows = session.exec(select(Evaluation).where(Evaluation.run_id == run.id).order_by(Evaluation.applicant_id)).all()
    return run, rows


def test_every_scored_applicant_gets_a_breakdown_traceable_to_the_runs_weights(session, settings):
    role = make_role(session, criteria=MIXED, approve=False)
    weights = {"skills": 60, "experience": 30, "education": 10, "certifications": 0}
    role.category_weights_json = __import__("json").dumps(weights)
    approve_role(session, role)
    applicants = [make_applicant(session, text) for text in (STRONG, MEDIUM, WEAK)]
    run, rows = screen(session, settings, role, applicants)

    assert loads(run.category_weights_json) == weights
    for evaluation in rows:
        breakdown = loads(evaluation.breakdown_json)
        assert [item["category"] for item in breakdown] == list(CATEGORIES)
        used = [item for item in breakdown if item["score"] is not None]
        assert {item["category"] for item in used} == {"skills", "experience", "education"}
        assert sum(item["share_pct"] for item in used) == pytest.approx(100, abs=0.2)
        penalty = 15 * (evaluation.must_haves_total - evaluation.must_haves_met)
        assert evaluation.fit_score == pytest.approx(max(0, sum(item["points"] for item in used) - penalty), abs=0.2)
    started = event_payload(list_events(session, run_id=run.id, event_types=["run_started"])[0])
    assert started["policy"]["category_weights"] == weights
    assert started["rubric"]["category_weights"] == weights
    scored = event_payload(list_events(session, run_id=run.id, event_types=["score_computed"])[0])
    assert len(scored["category_breakdown"]) == 4 and scored["criteria"][0]["category"] == "skills"


def test_same_resumes_same_weights_same_ranking_and_new_weights_reorder(session, settings):
    role = make_role(session, criteria=MIXED)
    applicants = [make_applicant(session, text) for text in (STRONG, MEDIUM, WEAK)]
    _, first = screen(session, settings, role, applicants)
    _, second = screen(session, settings, role, applicants)
    assert [(e.applicant_id, e.fit_score, e.rank, e.status) for e in first] == [
        (e.applicant_id, e.fit_score, e.rank, e.status) for e in second
    ]

    save_role(
        session, role, title=role.title, summary=role.summary, posting_text=role.posting_text,
        criteria=[CriterionInput.from_criterion(c) for c in current_criteria(session, role)],
        auto_reject_threshold=role.auto_reject_threshold, top_n=role.top_n,
        category_weights={"skills": 5, "experience": 5, "education": 90, "certifications": 0},
    )
    approve_role(session, role)
    _, reweighted = screen(session, settings, role, applicants)
    assert [e.fit_score for e in reweighted] != [e.fit_score for e in first]
