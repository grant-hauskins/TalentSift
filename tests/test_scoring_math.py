"""Fit score math, must-have penalty, and evidence matching, checked against hand calculations."""

import pytest

from talentsift.scoring import CriterionResult, compute_fit_score, normalize_for_match, verify_quote


def test_weighted_average_matches_hand_calculation():
    # high=3, medium=2, low=1. Points = 3*4 + 2*3 + 1*2 = 20 of a possible 4*(3+2+1) = 24 -> 83.33...
    fit = compute_fit_score(
        [
            CriterionResult(1, "must_have", "high", 4),
            CriterionResult(2, "must_have", "medium", 3),
            CriterionResult(3, "nice_to_have", "low", 2),
        ],
        must_have_penalty=15,
    )
    assert fit.raw_score == 83.3
    assert fit.fit_score == 83.3
    assert (fit.must_haves_met, fit.must_haves_total) == (2, 2)
    assert fit.penalty_points == 0


def test_must_have_penalty_matches_hand_calculation():
    # Points = 3*4 + 2*1 + 1*2 = 16 of 24 -> 66.67. One must-have scored 1 -> minus 15 -> 51.67 -> 51.7
    fit = compute_fit_score(
        [
            CriterionResult(1, "must_have", "high", 4),
            CriterionResult(2, "must_have", "medium", 1),
            CriterionResult(3, "nice_to_have", "low", 2),
        ],
        must_have_penalty=15,
    )
    assert fit.raw_score == 66.7
    assert fit.fit_score == 51.7
    assert fit.unmet_must_have_ids == (2,)
    assert (fit.must_haves_met, fit.must_haves_total) == (1, 2)


def test_two_unmet_must_haves_and_floor_at_zero():
    # Points = 3*0 + 3*1 + 1*1 = 4 of 28 -> 14.29. Two unmet must-haves -> 14.29 - 30 -> floor 0
    fit = compute_fit_score(
        [
            CriterionResult(1, "must_have", "high", 0),
            CriterionResult(2, "must_have", "high", 1),
            CriterionResult(3, "nice_to_have", "low", 1),
        ],
        must_have_penalty=15,
    )
    assert fit.raw_score == 14.3
    assert fit.penalty_points == 30
    assert fit.fit_score == 0.0


def test_must_have_scored_two_is_met_and_nice_to_have_zero_is_not_penalized():
    # Points = 3*2 + 2*0 = 6 of 20 -> 30.0. The must-have scored 2 is met; the nice-to-have costs nothing extra.
    fit = compute_fit_score(
        [CriterionResult(1, "must_have", "high", 2), CriterionResult(2, "nice_to_have", "medium", 0)],
        must_have_penalty=15,
    )
    assert fit.fit_score == 30.0
    assert fit.must_haves_met == 1


def test_penalty_is_configurable():
    results = [CriterionResult(1, "must_have", "high", 0), CriterionResult(2, "nice_to_have", "high", 4)]
    assert compute_fit_score(results, must_have_penalty=0).fit_score == 50.0
    assert compute_fit_score(results, must_have_penalty=10).fit_score == 40.0


def test_perfect_and_empty_scores():
    assert compute_fit_score([CriterionResult(1, "must_have", "low", 4)], 15).fit_score == 100.0
    assert compute_fit_score([], 15).fit_score == 0.0


SOURCE = normalize_for_match(
    "EXPERIENCE\n- Built  dashboards in Tableau\nand Power BI.\n- Presented findings to the VP’s team — monthly."
)


@pytest.mark.parametrize(
    "quote",
    [
        "Built dashboards in Tableau and Power BI",  # whitespace and line breaks normalized
        "built DASHBOARDS in tableau",  # case-insensitive
        '"Presented findings to the VP\'s team - monthly."',  # wrapping quotes, curly apostrophe, dash
    ],
)
def test_real_quotes_verify(quote):
    assert verify_quote(quote, SOURCE)


@pytest.mark.parametrize(
    "quote",
    [
        "Led a team of 12 data engineers",  # fabricated
        "Built dashboards in Looker",  # altered
        "Built dashboards ... monthly",  # stitched with an ellipsis
        "BI",  # too short to count as evidence
    ],
)
def test_fabricated_or_altered_quotes_fail(quote):
    assert not verify_quote(quote, SOURCE)


@pytest.mark.parametrize(
    ("quote", "source"),
    [
        ("Excel", "Excellent communicator with a friendly manner."),  # code review: a short quote inside a word
        ("SQL", "Built NoSQL document stores."),
        ("Java", "Wrote JavaScript front ends."),
    ],
)
def test_quotes_must_match_whole_words(quote, source):
    assert not verify_quote(quote, normalize_for_match(source))
    assert verify_quote(quote, normalize_for_match(f"Used {quote} daily."))


def test_reply_schema_is_part_of_the_cache_key(monkeypatch):
    from types import SimpleNamespace

    from talentsift import scoring

    role, prompt = SimpleNamespace(id=1, version=1), SimpleNamespace(cache_id="screen_v1@abc")
    before = scoring.cache_key_for("text", role, "rubric", prompt, "model")
    monkeypatch.setattr(scoring, "_SCHEMA_DIGEST", "edited-schema")
    assert scoring.cache_key_for("text", role, "rubric", prompt, "model") != before
