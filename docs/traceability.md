# Requirements traceability

Requirement -> module -> test. Update this table in the same pull request as the change (see the PR template).
Requirement text lives in [requirements.md](requirements.md). Test paths are relative to `tests/`.

## Functional requirements

| Req | Module(s) | Test(s) |
|-----|-----------|---------|
| FR-1 Posting -> AI-drafted rubric | `rubric.py`, `prompts/rubric_draft_v1.md`, `pages/1_Roles.py` | `test_roles_rubric.py::test_posting_becomes_a_draft_rubric`, `test_end_to_end.py::test_postings_become_approved_rubrics_without_proxy_criteria` |
| FR-2 Editable criteria (name, description, type, weight) | `roles.py::save_role`, `pages/1_Roles.py` | `test_roles_rubric.py::test_editing_a_draft_updates_in_place_and_marks_manager_rows`, `::test_invalid_rubrics_are_refused` |
| FR-3 Approval before screening; immutable approved versions | `roles.py::approve_role`, `scoring.py::run_screening` | `test_roles_rubric.py::test_approved_rubric_is_immutable_and_edits_create_a_new_version`, `test_screening_run.py::test_screening_requires_an_approved_rubric` |
| FR-4 Create, edit, duplicate, version; threshold and top N | `roles.py` | `test_roles_rubric.py::test_duplicate_role_copies_rubric_as_new_draft`, `::test_threshold_change_does_not_create_a_version`, `::test_discard_draft_returns_to_approved_version`, `::test_redraft_on_approved_role_creates_ai_draft_version` |
| FR-5 Proxy flags in rubric drafting | `rubric.py`, `llm/fake_client.py::PROXY_RULES`, `prompts/rubric_draft_v1.md` | `test_llm_validation.py::test_fake_rubric_draft_follows_posting_and_flags_proxies`, `test_roles_rubric.py::test_posting_becomes_a_draft_rubric` |
| FR-6 Upload and folder import; duplicates skipped | `ingest.py`, `pages/2_Applicants.py` | `test_ingest.py::test_folder_import_records_every_supported_file`, `::test_upload_is_saved_and_ingested`, `::test_duplicates_are_skipped`, `::test_applicant_stores_filename_hash_text_and_masked_text` |
| FR-7 `needs_ocr` and `partial` detection | `parsers/pdf.py` | `test_parsers.py::test_near_empty_pdf_is_flagged_needs_ocr`, `::test_pdf_with_a_scanned_page_is_partial`, `::test_committed_sample_resumes_parse` |
| FR-8 Pluggable parsers; DOCX stub | `parsers/base.py`, `parsers/registry.py`, `parsers/docx.py` | `test_parsers.py::test_new_parser_plugs_in_without_touching_scoring`, `::test_docx_stub_reports_planned`, `::test_registry_picks_parser_by_extension` |
| FR-9 PII masking; optional grad years | `masking.py` | `test_masking.py` (all), `test_parsers.py::test_photo_is_dropped_from_text` |
| FR-10 One call per applicant, temperature 0, versioned prompt | `scoring.py`, `prompts.py`, `prompts/screen_v1.md` | `test_screening_run.py::test_llm_only_sees_masked_text_and_label`, `test_openrouter_client.py::test_request_shape_matches_openrouter_structured_outputs` |
| FR-11 Validate, retry once, `validation_failed` -> review | `llm/base.py::call_with_validation`, `schemas.py` | `test_llm_validation.py::test_schema_rejects_malformed_output`, `::test_retry_path_recovers_from_one_malformed_reply`, `::test_two_malformed_replies_fail_validation`, `test_screening_run.py::test_validation_failure_after_retry_goes_to_review` |
| FR-12 Evidence verification blocks auto-reject | `scoring.py::verify_quote` | `test_scoring_math.py::test_real_quotes_verify`, `::test_fabricated_or_altered_quotes_fail`, `test_screening_run.py::test_fabricated_quote_goes_to_review_not_auto_reject` |
| FR-13 Fit score in code; must-have penalty | `scoring.py::compute_fit_score` | `test_scoring_math.py` (hand-calculated examples) |
| FR-14 Deterministic ranking | `scoring.py::ranking_key` | `test_status.py::test_ranking_order_uses_must_haves_then_applicant_id`, `test_end_to_end.py::test_rerun_gives_same_ranking_and_statuses` |
| FR-15 Status assignment | `scoring.py::assign_statuses` | `test_status.py::test_top_n_threshold_and_middle`, `::test_threshold_beats_top_n`, `::test_ties_at_the_cutoff_are_all_shortlisted`, `::test_exactly_at_threshold_is_not_rejected` |
| FR-16 Guardrails; automatic and confirm modes | `scoring.py`, `overrides.py::confirm_pending_rejections` | `test_status.py::test_guardrail_flags_never_auto_reject`, `::test_automatic_mode_end_to_end`, `::test_confirm_mode_end_to_end`, `test_screening_run.py::test_partial_and_unparsed_resumes_are_never_auto_rejected`, `::test_truncated_resume_goes_to_review` |
| FR-17 Job-related reasons | `scoring.py::build_reasons` | `test_status.py::test_reasons_are_specific_and_job_related` |
| FR-18 Results tabs, cards, detail | `pages/4_Results.py` | `test_ui_smoke.py::test_screen_run_and_reinstate_flow` |
| FR-19 Overrides and reinstatement with reasons | `overrides.py`, `pages/4_Results.py` | `test_audit.py::test_override_without_a_real_reason_is_rejected`, `::test_reinstatement_is_logged_and_reversible`, `test_ui_smoke.py::test_screen_run_and_reinstate_flow` |
| FR-20 Append-only audit log, filters, viewer, CSV export | `audit.py`, `db.py`, `pages/5_Audit.py` | `test_db.py::test_audit_event_rejects_sql_update_and_delete`, `::test_audit_event_rejects_orm_update_and_delete`, `test_audit.py::test_no_update_or_delete_code_paths_for_audit_tables`, `::test_csv_export_contains_events_and_is_logged` |
| FR-21 Result cache and force re-score | `scoring.py::cache_key_for`, `scoring.py::_find_cached` | `test_screening_run.py::test_rerun_is_identical_and_served_from_cache` |
| FR-22 Token and cost tally; cost cap | `scoring.py::_Budget`, `pages/3_Screen.py` | `test_screening_run.py::test_cost_cap_stops_the_run_cleanly` |
| FR-23 Fairness checks | `fairness.py`, `pages/5_Audit.py` | `test_fairness.py` (all), `test_ui_smoke.py::test_demo_loader_and_fairness_checks_from_the_ui` |
| FR-24 OpenRouter client, provider-agnostic interface, fallback | `llm/base.py`, `llm/openrouter_client.py`, `llm/factory.py` | `test_openrouter_client.py` (all) |
| FR-25 Offline deterministic client | `llm/fake_client.py` | `test_llm_validation.py::test_fake_client_is_deterministic`, `::test_fake_screening_quotes_resume_lines_verbatim` |
| FR-26 Banner on every page | `ui.py::page_setup`, every page | `test_ui_smoke.py::test_empty_app_renders_every_page_with_banner_and_provider_picker` |
| FR-27 Sample data and seeding | `scripts/make_sample_resumes.py`, `demo.py`, `scripts/seed_demo.py` | `test_end_to_end.py` (all), `test_parsers.py::test_committed_sample_resumes_parse` |

| FR-29 Criterion categories | `models.py::CATEGORIES`, `roles.py`, `prompts/rubric_draft_v2.md`, `llm/fake_client.py::infer_category` | `test_category_weights.py::test_changing_weights_on_an_approved_rubric_creates_a_new_version_and_discard_restores_them`, `::test_duplicate_keeps_categories_and_weights` |
| FR-30 Employer category weights, versioned | `roles.py::clean_category_weights`, `roles.py::save_role`, `pages/1_Roles.py` | `test_category_weights.py::test_role_stores_weights_and_rejects_bad_ones`, `test_ui_smoke.py::test_roles_page_sets_category_weights_and_results_show_the_breakdown` |
| FR-31 Category-weighted fit score | `scoring.py::compute_fit_score` | `test_category_weights.py::test_fit_score_combines_category_scores_by_the_employers_weights`, `::test_weights_change_the_score_and_only_relative_size_matters`, `::test_one_category_gives_the_original_weighted_average`, `::test_must_have_penalty_applies_after_the_category_weights` |
| FR-32 Score breakdown for every candidate | `scoring.py::CategoryResult`, `pages/4_Results.py::breakdown_block` | `test_category_weights.py::test_every_scored_applicant_gets_a_breakdown_traceable_to_the_runs_weights`, `test_end_to_end.py::test_sow_success_criterion_25_resumes_ranked_with_a_breakdown_for_every_candidate` |
| FR-33 Choice of AI model | `model_catalog.py`, `pages/6_Settings.py` | `test_ui_smoke.py::test_settings_page_offers_the_model_list_with_claude_as_default` |
| FR-34 Consent and access code | `candidates.py::create_profile`, `pages/7_Candidate_Profile.py` | `test_candidates.py::test_a_profile_needs_consent_and_a_name`, `::test_consent_is_recorded_and_the_code_opens_the_profile`, `test_ui_smoke.py::test_candidate_cannot_start_without_consent` |
| FR-35 Core facts | `candidates.py`, `pages/7_Candidate_Profile.py` | `test_candidates.py::test_facts_are_stored_by_category_edited_and_removed`, `::test_location_must_not_be_a_street_address_or_zip` |
| FR-36 Fact-anchored drafting | `drafting.py`, `prompts/resume_draft_v1.md`, `schemas.py::ResumeDraft` | `test_drafting.py::test_draft_uses_only_the_facts_and_code_renders_titles_employers_and_dates`, `::test_the_model_never_receives_the_candidates_name_or_contact_details` |
| FR-37 No-new-facts guard | `guard.py` | `test_guard.py` (all) |
| FR-38 Retry, then fallback | `drafting.py::generate_draft`, `draft_template.py` | `test_drafting.py::test_an_invented_draft_is_retried_then_replaced_by_the_candidates_own_wording`, `::test_a_clean_retry_is_accepted`, `::test_ai_outage_falls_back_to_the_fact_template`, `test_guard.py::test_the_fact_template_always_passes` |
| FR-39 Approval before save | `drafting.py::save_version`, `pages/8_Candidate_Draft.py` | `test_drafting.py::test_saving_needs_approval_a_unique_name_and_a_draft_that_still_matches_the_facts` |
| FR-40 Versions and compare | `drafting.py::compare_versions`, `pages/9_Candidate_Versions.py` | `test_drafting.py::test_versions_are_listed_compared_and_deleted`, `test_ui_smoke.py::test_candidate_flow_profile_facts_draft_approve_save_compare` |
| FR-41 Delete my data; separation; no content in audit | `candidates.py::delete_profile`, `drafting.py` | `test_candidates.py::test_delete_removes_everything_and_the_audit_log_never_held_personal_data`, `test_drafting.py::test_audit_log_records_the_check_but_none_of_the_candidates_content`, `test_end_to_end.py::test_candidate_flow_end_to_end_stays_separate_from_employer_data` |
| FR-42 Unmasked name never reaches the model | `masking.py::name_was_masked`, `scoring.py::_name_exposed` | `test_defect_fixes.py::test_resume_without_a_masked_name_is_not_sent_to_the_model`, `test_masking_hardening.py` (all) |
| FR-43 Backup and restore | `backup.py`, `scripts/backup.py`, `config.py` | `test_backup.py` (all) |
| FR-44 Schema upgrade in place | `db.py::upgrade_schema` | `test_schema_upgrade.py` (all) |
| FR-45 A/B scoring of versions | `version_scoring.py`, `rubric.py::draft_criteria`, `pages/9_Candidate_Versions.py` | `test_version_scoring.py::test_the_version_with_more_relevant_facts_scores_higher`, `::test_scoring_is_repeatable`, `::test_the_model_sees_no_name_or_contact_details_and_employer_data_is_untouched`, `::test_evidence_not_found_in_the_version_is_flagged` |
| FR-46 Suggestions with nothing added | `suggestions.py` | `test_version_scoring.py::test_suggestions_point_to_the_candidates_own_facts_that_a_version_left_out`, `::test_a_requirement_the_facts_do_not_cover_is_reported_as_a_gap_never_filled_in`, `::test_every_quoted_line_comes_verbatim_from_the_facts_and_must_haves_come_first` |

## Non-functional requirements

| Req | Module(s) | Test(s) |
|-----|-----------|---------|
| NFR-1 Privacy | `masking.py`, `scoring.py::_build_job`, `ingest.py` | `test_screening_run.py::test_llm_only_sees_masked_text_and_label`, `test_audit.py::test_audit_payloads_hold_no_raw_pii`, `test_ingest.py::test_ingest_writes_audit_events_without_pii` |
| NFR-2 Repeatability | `scoring.py`, `llm/openrouter_client.py` (temperature 0) | `test_screening_run.py::test_rerun_is_identical_and_served_from_cache`, `test_fairness.py::test_consistency_check_passes_with_deterministic_client` |
| NFR-3 Auditability | `audit.py`, `db.py`, `scoring.py` | `test_screening_run.py::test_every_decision_traces_to_audit_events`, `test_end_to_end.py::test_scores_and_rejections_trace_to_verified_evidence`, `test_audit.py::test_audit_module_exposes_no_mutation_functions` |
| NFR-4 Reversibility | `overrides.py` | `test_end_to_end.py::test_auto_rejected_applicant_can_be_reinstated_with_logged_reason` |
| NFR-5 Scale (200 applicants) | `scoring.py::iter_scored` (parallel calls), cache, cost cap | `test_screening_run.py::test_cost_cap_stops_the_run_cleanly` (concurrency exercised by every run test) |
| NFR-6 Fairness | `prompts/screen_v1.md`, `scoring.py::assign_statuses`, `masking.py` | `test_status.py::test_ties_at_the_cutoff_are_all_shortlisted`, `test_fairness.py::test_name_swap_pair_yields_identical_masked_text_and_status`, `test_masking.py::test_name_swap_pair_masks_identically` |
| NFR-7 Maintainability | package layout, `CONTRIBUTING.md` | code review in pull requests |
| NFR-8 Portability | `config.py`, `ui.py::load_streamlit_secrets` | `test_db.py::test_settings_from_env_parses_values`, `::test_file_database_is_created_with_parent_folder` |
| NFR-9 Quality gates | `.github/workflows/tests.yml` | the whole suite, offline |
| NFR-10 Secrets | `config.py` (key excluded from `repr`) | `test_db.py::test_settings_from_env_parses_values`, `test_openrouter_client.py::test_result_reports_model_provider_tokens_and_cost` |
| NFR-11 Explainability | `scoring.py::build_reasons` | `test_status.py::test_reasons_are_specific_and_job_related` |
| NFR-12 Resilience | `llm/openrouter_client.py`, `scoring.py::_Budget` | `test_openrouter_client.py::test_retries_once_on_network_failure`, `::test_bad_key_is_fatal`, `test_screening_run.py::test_fatal_provider_error_stops_calls_and_fails_the_run` |
| NFR-13 Truthful drafts | `guard.py`, `schemas.py::ResumeDraft`, `draft_template.py` | `test_guard.py::test_anything_beyond_the_facts_is_blocked`, `test_drafting.py::test_an_invented_draft_is_retried_then_replaced_by_the_candidates_own_wording` |
| NFR-14 Separation of sides | `models.py` (candidate tables), `candidates.py`, `ui.py::current_candidate` | `test_end_to_end.py::test_candidate_flow_end_to_end_stays_separate_from_employer_data` |
| NFR-15 Speed: 25 resumes in under five minutes | `scoring.py` (parallel calls, cache) | `test_end_to_end.py::test_sow_success_criterion_25_resumes_ranked_with_a_breakdown_for_every_candidate` |

## Definition of done

| Item | Evidence |
|------|----------|
| A pasted posting becomes an approved rubric; the same folder screened for two roles gives two different, explainable rankings | `test_end_to_end.py::test_postings_become_approved_rubrics_without_proxy_criteria`, `::test_same_folder_two_roles_two_explainable_rankings` |
| Every score and auto-rejection traces to audit events with verified evidence | `test_end_to_end.py::test_scores_and_rejections_trace_to_verified_evidence` |
| Re-running a screen yields the same ranking and statuses | `test_end_to_end.py::test_rerun_gives_same_ranking_and_statuses` |
| An auto-rejected applicant can be reinstated with a logged reason | `test_end_to_end.py::test_auto_rejected_applicant_can_be_reinstated_with_logged_reason`, `test_ui_smoke.py::test_screen_run_and_reinstate_flow` |
| All tests pass in CI; the app runs offline with the fake client | `.github/workflows/tests.yml` (runs with `LLM_PROVIDER=fake`), `test_ui_smoke.py` |
| `docs/` has requirements, traceability, decisions, and architecture | this folder |
