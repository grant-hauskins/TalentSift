# Requirements

Numbered requirements for the TalentSift MVP. Each one maps to modules and tests in
[traceability.md](traceability.md). Status values: **Done** (built and tested), **Planned** (final project).

The user is always a hiring manager, never an applicant.

## Functional requirements

| ID | Requirement | Status |
|----|-------------|--------|
| FR-1 | The manager can create a role by pasting a job posting; the AI drafts a rubric of 5-10 criteria. | Done |
| FR-2 | Each criterion has a `name`, `description`, `type` (`must_have` / `nice_to_have`) and `weight` (`high` / `medium` / `low`); all are editable, and criteria can be added or removed. | Done |
| FR-3 | A rubric version must be approved by the manager before it can be used for screening. Approved versions never change; editing creates a new draft version. | Done |
| FR-4 | Roles can be created, edited, duplicated, and versioned. Each role has an `auto_reject_threshold` (0-100, default 40) and `top_n` (default 5). | Done |
| FR-5 | Rubric drafting flags any criterion that could act as a proxy for a protected characteristic. | Done |
| FR-6 | Resumes can be ingested by multi-file upload or by importing every PDF in a folder path. The app stores the original filename, file hash, extracted text, masked text, and parse status. Duplicate files are skipped. | Done |
| FR-7 | A PDF that yields almost no text is marked `needs_ocr` and skipped with a clear message. A PDF with some text-less scanned pages is marked `partial`. | Done |
| FR-8 | New file formats plug in through a parser registry without changes to scoring code. DOCX has a stub parser. | Done |
| FR-9 | Before scoring, the app masks names (first-line heuristic plus every later occurrence), emails, phone numbers, street addresses including ZIP codes, URLs and social links, and image content. Graduation years are masked only when `MASK_GRAD_YEARS=true`. | Done |
| FR-10 | For a chosen approved role and batch, the app scores every screenable resume against every criterion with one LLM call per applicant at temperature 0, using a versioned prompt. | Done |
| FR-11 | LLM output is validated against a schema. On failure the app retries once, then logs `validation_failed` and sets the applicant to `needs_review`. | Done |
| FR-12 | Every evidence quote must appear in the masked resume text (case and whitespace normalized). Unverified quotes are flagged in the UI, logged as `evidence_unverified`, and block auto-rejection. | Done |
| FR-13 | The fit score (0-100) is computed in code as a weighted average of criterion scores (high=3, medium=2, low=1). Each must-have scored 0-1 subtracts `MUST_HAVE_PENALTY` points (default 15, floor 0). | Done |
| FR-14 | Ranking is deterministic: fit score, then must-haves met, then applicant id. Never by name. | Done |
| FR-15 | Statuses: top N eligible applicants are `shortlisted`; applicants below the threshold are `auto_rejected` (subject to guardrails); everything else is `not_shortlisted`; guardrail cases are `needs_review`. | Done |
| FR-16 | Auto-reject guardrails: never auto-reject an applicant whose evaluation failed validation, has unverified evidence, or was not fully parsed. `AUTO_REJECT_MODE` is `automatic` (default) or `confirm` (manager approves the batch first). | Done |
| FR-17 | Auto-rejected and not-shortlisted applicants get specific, job-related reasons: unmet must-haves, weakest criteria, and a short explanation, written so they could be shared with applicants. | Done |
| FR-18 | The Results page has Shortlist, Not shortlisted, Auto-rejected, and Needs review tabs. Shortlist cards show fit score, must-have checklist, and strengths with evidence quotes. Candidate detail shows per-criterion scores. | Done |
| FR-19 | The manager can override any status and reinstate an auto-rejected applicant in one click. A reason is required, and each change is logged as its own `override` event. | Done |
| FR-20 | Every step writes an append-only audit event. The log is viewable, filterable by run, applicant, and event type, has a raw prompt and response viewer, and exports to CSV (the export itself is logged). | Done |
| FR-21 | Results are cached by a hash of masked text, role version, prompt version, and model, so re-runs are consistent and cheap. A "force re-score" option bypasses the cache. | Done |
| FR-22 | The Screen page shows progress and a live token and cost tally. A per-batch cost cap stops the run cleanly. | Done |
| FR-23 | The Audit page offers two fairness checks: a consistency re-run on a sample (comparing rankings and statuses) and a name-swap test (identical masked text, scores, and status). | Done |
| FR-24 | The AI provider is OpenRouter through the `openai` SDK, with the model slug from `LLM_MODEL`, behind a provider-agnostic `LLMClient` interface. If a model rejects structured outputs, the client falls back to plain JSON instructions and logs the fallback. | Done |
| FR-25 | A deterministic offline client lets the app run, demo, and test without network access. | Done |
| FR-26 | Every page shows the banner: "AI recommendations and auto-rejections are logged and reversible. Review results before contacting applicants." | Done |
| FR-27 | Scripts generate 20 fictional sample resumes and 2 role profiles, and seed a demo database. | Done |
| FR-28 | DOCX and OCR ingestion, authentication, multi-user roles, emailing candidates, ATS integrations, side-by-side compare, fairness dashboards. | Planned |

## Non-functional requirements

| ID | Requirement | Status |
|----|-------------|--------|
| NFR-1 | **Privacy.** The LLM only ever sees masked text and the display label. Audit payloads contain no raw PII (no names, contact details, or original filenames). The repository contains fictional data only. | Done |
| NFR-2 | **Repeatability.** Temperature 0, versioned prompts, and result caching make a re-run produce the same ranking and statuses. | Done |
| NFR-3 | **Auditability.** Every score and automated decision traces to audit events. Append-only storage is enforced in the database (triggers) and in the ORM. | Done |
| NFR-4 | **Reversibility.** Every automated decision can be reversed by a human with a logged reason. | Done |
| NFR-5 | **Scale.** Designed for 200 applicants per role (parallel LLM calls, caching, cost cap); demonstrated with 20. | Done |
| NFR-6 | **Fairness.** Prompts forbid inferring protected characteristics and rewarding prestige; ranking never uses names; ties at the shortlist cutoff are never broken by upload order. | Done |
| NFR-7 | **Maintainability.** Small modules split by owner (data, AI, UX) so three people can work in parallel; type hints and comments throughout. | Done |
| NFR-8 | **Portability.** Python 3.11+, runs locally with `streamlit run app.py`; optional Streamlit Community Cloud deploy. | Done |
| NFR-9 | **Quality gates.** The full pytest suite runs offline in GitHub Actions on every pull request. | Done |
| NFR-10 | **Secrets.** API keys come only from `.env` or the hosting secrets manager and are never logged or committed. | Done |
| NFR-11 | **Explainability.** Reasons are specific, job-related, respectful, and never reference personal characteristics. | Done |
| NFR-12 | **Resilience.** LLM calls have timeouts and one retry on network failure; fatal provider errors stop the run with a clear message instead of failing every applicant. | Done |
