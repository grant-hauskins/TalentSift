# Decision log

Short, dated records of decisions that shape the product or the code. Newest decisions go at the bottom.
To change a decision, add a new entry that supersedes the old one; do not edit history.

Format: **ID - title** (date, decided by). Context, decision, consequences.

## Team decisions (locked 2026-10-01)

### D-001 - AI provider: OpenRouter through the `openai` SDK (2026-10-01, team)
- **Context:** We want to compare models and providers without rewriting code.
- **Decision:** Use the `openai` Python SDK with `base_url="https://openrouter.ai/api/v1"`. The model slug comes
  from `LLM_MODEL`, never from code. Requests ask for strict JSON-schema output and set
  `provider.require_parameters=true` so OpenRouter only routes to providers that honor it.
- **Consequences:** One key, many models. Some models reject structured outputs, so the client falls back to plain
  JSON instructions plus Pydantic validation and logs `llm_fallback`. Everything sits behind `LLMClient`, so a
  direct provider SDK can replace OpenRouter later.

### D-002 - Auto-reject is on by default, with guardrails (2026-10-01, team)
- **Context:** Managers screening 200 applicants need the clear non-fits handled automatically.
- **Decision:** `AUTO_REJECT_MODE=automatic` by default. Applicants below the role's threshold are auto-rejected
  with job-related reasons. A `confirm` mode makes the manager approve each batch of rejections first.
- **Consequences:** Guardrails are mandatory: failed validation, unverified evidence, and incomplete parsing go to
  `needs_review`. Every rejection writes an `auto_reject` event and can be reversed with one click and a reason.

### D-003 - Must-haves lower the score instead of knocking applicants out (2026-10-01, team)
- **Context:** Hard knock-outs amplify any single scoring mistake.
- **Decision:** Each must-have scored 0-1 subtracts `MUST_HAVE_PENALTY` points (default 15, floor 0).
- **Consequences:** An applicant missing one must-have can still rank well if strong elsewhere. The penalty is a
  setting, so the team can tune it with evidence.

### D-004 - Rubrics are AI-drafted and manager-approved (2026-10-01, team)
- **Context:** Writing rubrics from scratch is slow; unreviewed AI rubrics are risky.
- **Decision:** The AI proposes 5-10 criteria from the pasted posting and flags possible proxies for protected
  characteristics. Screening is blocked until the manager approves the rubric.
- **Consequences:** Each criterion records its `source` (`ai_draft` or `manager`), and approvals are logged.

### D-005 - Local-first deployment; optional Streamlit Community Cloud; not Vercel (2026-10-01, team)
- **Context:** Streamlit needs a long-lived server session; Vercel functions are short-lived and do not keep a
  SQLite file between requests.
- **Decision:** Run locally with `streamlit run app.py`. A hosted demo may use Streamlit Community Cloud with its
  secrets manager.
- **Consequences:** Hosted disks reset on reboot, so `AUTO_SEED_DEMO` and a "Load demo data" button rebuild the
  demo. Real applicant data stays on the manager's machine.

### D-006 - SDLC practices are part of the deliverable (2026-10-01, team)
- **Decision:** Requirements, traceability, decisions, and architecture live in `docs/`. One branch per WBS task
  (`wbs-<id>-<slug>`), conventional commits, PR and issue templates, a changelog, and CI running pytest on every
  pull request.

## Build decisions (2026-10-01, made during the MVP build)

### D-007 - The LLM scores criteria; code computes fit, rank, and status
- **Decision:** The model returns 0-4 scores with rationales and quotes. Fit score, ranking, and status are
  computed in `scoring.py`.
- **Consequences:** Decisions are reproducible and unit-testable; the math is visible in one place.

### D-008 - The auto-reject threshold takes precedence over top N
- **Context:** With few applicants, a "top 5" could include someone below the threshold.
- **Decision:** Only applicants at or above the threshold can be shortlisted.
- **Consequences:** A shortlist can be shorter than N. Nobody below the minimum is presented as a top candidate.

### D-009 - Ties at the shortlist cutoff are all shortlisted
- **Context:** Ranking breaks ties by applicant id, which reflects upload order, not merit. The name-swap pair has
  identical scores and must not get different statuses.
- **Decision:** An applicant whose (fit score, must-haves met) equals the N-th shortlisted applicant's is also
  shortlisted, so the shortlist can exceed N on exact ties.
- **Consequences:** Statuses never depend on upload order; rank numbers still order the list deterministically.
  With coarse 0-4 scores, ties are common, so the shortlist can exceed N (in a stress test with 200 near-duplicate
  resumes and N = 10, 66 tied applicants were shortlisted). The Results page says when ties enlarged the shortlist.

### D-010 - Every guardrail case goes to `needs_review`, not only would-be rejections
- **Decision:** Failed validation, unverified evidence, partial parses, truncated input, and applicants left
  unscored (cost cap or provider error) all go to `needs_review`, and only fully verified evaluations are ranked.
- **Consequences:** The shortlist contains only verified evidence. The manager resolves review cases with an override.

### D-011 - Approved rubric versions are immutable
- **Decision:** Criterion rows carry `role_version`. Saving changes to an approved version creates version N+1 as a
  draft; screening requires the current version to be approved. Drafts are edited in place.
- **Consequences:** Every past run can show the exact rubric it used. Threshold and top N are policy settings,
  not rubric content: changing them does not create a version, and each run snapshots the values it used.

### D-012 - Cache key covers everything that changes the model's input
- **Decision:** The key hashes masked text, role id, role version, a hash of the rubric content, the prompt
  version plus a hash of the prompt file, and the model slug. Only validated replies are reused.
- **Consequences:** Editing a prompt without bumping its version cannot serve stale results. Two different roles at
  the same version number never collide. The display label is excluded on purpose, so identical masked resumes
  get identical evaluations; the prompt tells the model not to repeat the label.

### D-013 - Confirm mode uses a `pending_auto_reject` status
- **Decision:** In `confirm` mode, below-threshold applicants get `pending_auto_reject`. The manager confirms the
  batch (writing the `auto_reject` events) or keeps individuals with an override.

### D-014 - Extra audit event types and append-only enforcement
- **Decision:** Beyond the brief's list we log `role_saved`, `run_started`, `run_finished`, `llm_fallback`, and
  `fairness_check`. The `audit_event` and `override` tables reject UPDATE and DELETE through SQLite triggers, and an
  ORM hook rejects edits before SQL is sent.

### D-015 - No raw PII in audit payloads
- **Decision:** Audit payloads use display labels, hashes, counts, and masked text. Original filenames (which often
  contain names) stay in the `applicant` table only.

### D-016 - A positive criterion score must cite evidence
- **Decision:** Semantic validation rejects any criterion scored 1-4 without at least one quote, and any reply
  that skips or invents criterion ids. Score 0 means "no evidence found".
- **Consequences:** The retry message tells the model exactly what to fix; a second failure goes to review.

### D-017 - The offline fake client is a deterministic keyword matcher
- **Decision:** `FakeLLMClient` reads the same prompt a real model gets and scores criteria by keyword coverage,
  quoting matching resume lines verbatim. It can also replay scripted replies for tests.
- **Consequences:** Tests and the demo backup run offline with explainable, repeatable results.

### D-018 - Definition of "not fully parsed"
- **Decision:** A PDF page with images but almost no text counts as a scanned page, making the resume `partial`.
  Resumes longer than `MAX_RESUME_CHARS` are truncated and flagged. Both route to `needs_review`.

### D-019 - Parallel LLM calls, single-threaded database writes
- **Decision:** Up to `LLM_CONCURRENCY` calls run in parallel threads that never touch the database. Results are
  written in applicant order on the main thread.
- **Consequences:** 200 applicants finish in minutes, audit order is stable, and SQLite never sees concurrent
  writers. The cost cap is checked before each call, so it can overshoot by at most the in-flight calls.

### D-020 - Initial MVP built on one integration branch
- **Context:** The MVP scaffold was generated in a single build session restricted to one branch.
- **Decision:** The initial build landed on one branch with one conventional commit per build step, each tagged
  with its WBS ids. From here on, every task gets its own `wbs-<id>-<slug>` branch and pull request.

### D-021 - Classic `pages/` multipage app instead of `st.navigation`
- **Context:** With `st.navigation` in `app.py` and a `pages/` folder, a fresh session that opened a page URL
  directly (for example `/Results` right after a restart) skipped `app.py`, so it lost the sidebar picker and the
  wide layout until someone visited Home.
- **Decision:** Use Streamlit's classic `pages/` folder. Every page, including `app.py`, starts with
  `ui.page_setup`, which sets the page config and draws the disclaimer banner and the AI provider picker.
- **Consequences:** The banner and picker appear on every page however it is opened, and pages test in isolation.
  The home entry in the sidebar is labeled "app" (Streamlit names it after the entry file).

### D-022 - Demo seeding removes AI-flagged proxy criteria before approving
- **Decision:** `seed_demo` drafts each sample role with the AI, deletes any criterion flagged as a possible
  proxy (the Operations posting plants "Recent graduate preferred"), then approves, as a careful manager would.
  The manual flow on the Roles page keeps the flag visible so the manager makes that call.
