# Fairness and guardrail validation report (T29, WBS 7.3)

**Bottom line:** every SOW critical success factor has an automated check that passes offline. Two things are
still open and need a person: a timed batch on the real model, and UAT with pilot users.

Date: November 9, 2026. Build: branch `claude/peaceful-brahmagupta-97bdet`. Suite: 363 tests, all passing on
Python 3.11 and 3.13 with the offline client. Data: the 26 committed fictional resumes and two sample roles.

## Critical success factors

| SOW factor | What was checked | Result | Evidence |
|------------|------------------|--------|----------|
| **Consistent, explainable scoring:** identical rankings when scored twice, traceable to the employer's weights | Both sample roles re-scored with the cache off: scores, ranks, statuses, and breakdowns identical. Every scored applicant has a breakdown whose points add up to the fit score and whose weights equal the run's weights. | Pass | `test_end_to_end.py::test_repeat_scoring_gives_identical_rankings_even_without_the_cache`, `test_category_weights.py::test_every_scored_applicant_gets_a_breakdown_traceable_to_the_runs_weights` |
| **Fair screening:** names, street addresses, ZIP codes removed before ranking | All 50 scoring prompts for the sample set contain no applicant's own name, street address, ZIP, email, or phone. 33 audited header layouts (16 name, 17 address) are masked, and 8 job-text lines the old rules damaged are left intact. A resume with no findable name is held back from the model. | Pass | `test_end_to_end.py::test_no_name_street_address_or_zip_reaches_any_scoring_prompt`, `test_masking_hardening.py`, `test_defect_fixes.py::test_resume_without_a_masked_name_is_not_sent_to_the_model` |
| **Truthful drafts:** nothing beyond the core facts | 13 seeded invented-fact drafts (new skill, tool, number, credential, posting term) each blocked for the expected reason. A model that invents twice is replaced by the candidate's own wording. | Pass | `test_guard.py::test_anything_beyond_the_facts_is_blocked`, `test_drafting.py::test_an_invented_draft_is_retried_then_replaced_by_the_candidates_own_wording` |
| **Ease of use:** a ranked shortlist with no training | Not testable in code. | **Open: UAT** | `docs/uat_script.md` |

## Success criteria

| Criterion | Result |
|-----------|--------|
| Rank a batch of at least 25 resumes in under five minutes | **Offline: pass.** 26 PDFs parsed and masked in 0.4 s; 25 scored and ranked in 0.2 s (one image-only resume correctly skipped). **Real model: not yet measured.** With 4 parallel calls (`LLM_CONCURRENCY`), 25 calls fit in five minutes if a call averages under 48 seconds; the timeout is 60. Record the real time at T28. |
| Score breakdown for every candidate | Pass (see above). |
| Demonstrate both sides | Both flows run end to end in headless UI tests: `test_ui_smoke.py::test_roles_page_sets_category_weights_and_results_show_the_breakdown`, `::test_candidate_flow_profile_facts_draft_approve_save_compare`. |

## Fairness checks carried over from v1

| Check | Result | Evidence |
|-------|--------|----------|
| Name-swap pair (resumes 17 and 18, identical except name and contact) gets identical masked text, score, and status | Pass, with category weights applied | `test_fairness.py`, `test_end_to_end.py` |
| Consistency re-run now uses the run's own penalty and model | Pass (defect D-08 fixed) | `test_defect_fixes.py::test_consistency_check_uses_the_runs_own_penalty_and_model` |
| Auto-reject guardrails: unparsed, partial, unverified, or unmasked resumes never auto-reject | Pass | `test_status.py`, `test_screening_run.py`, `test_defect_fixes.py` |
| Proxy criteria flagged before approval ("Recent graduate preferred") | Pass | `test_end_to_end.py::test_postings_become_approved_rubrics_without_proxy_criteria` |

## Limits of this validation

- **Everything above ran on the offline client**, a keyword matcher. It proves the code paths, the math, and the
  guardrails. It says nothing about the real model's scoring quality or how often its drafts trip the guard.
- **The sample set is small and synthetic**: 26 fictional resumes in one layout. The masking cases are
  hand-written from the audit, not drawn from real resumes.
- **Category weights can move protected-class proxies.** A heavy education weight favors degree holders. The
  weights are the employer's choice; they are shown, logged, and versioned, not judged.

## Still to do before M5

1. One live batch of the 26 samples on the default model: record time, cost, and any validation failures.
2. Ten live drafts: record how many pass the guard first time, pass on retry, or fall back.
3. UAT sessions (T30) and the defect cycle (T31).
