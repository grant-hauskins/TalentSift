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

## MVP build sprint (November 2026)

Decisions D-023 to D-033 were made in code during the MVP build and are **proposed**: they need the three
founders' agreement at the next status meeting, like any other sponsor-level call (Planning Report section 6.1).

### D-023 - Category weights are shares, not multipliers (2026-11-09, proposed)
- **Context:** The SOW promises adjustable weights for skills, education, certifications, and experience, with
  rankings "traceable to the employer's weights".
- **Decision:** Each criterion has one category. A category's score is the weighted average of its criteria. The
  employer's four weights become shares of the categories the rubric actually uses, and the fit score is the sum
  of share x category score, minus the must-have penalty (D-003 unchanged).
- **Consequences:** The breakdown reads as plain arithmetic ("Skills 82 x 60% = 49.2 points"). A rubric with every
  criterion in one category scores exactly as the prototype did, so v1 results are unchanged. A category with one
  minor criterion still gets its full share: that is what the employer asked for, and the Roles page shows the
  split before approval.

### D-024 - Weights are part of the rubric version (2026-11-09, proposed)
- **Decision:** Changing weights on an approved rubric creates a new draft version (like editing a criterion).
  Each run also stores the weights it used.
- **Consequences:** Two runs of the same version always use the same weights. Weights are in the rubric hash, so
  cached model replies are not reused across a weight change even though the model never sees weights. That
  costs some API spend and keeps the cache rule simple (D-012).

### D-025 - A short model list in code, with Claude as the default (2026-11-09, proposed; refines D-001)
- **Context:** D-001 kept model names out of code. The SOW adds "choice of AI model" with Claude as default.
- **Decision:** `talentsift/model_catalog.py` is the only place that names models. The Settings page offers that
  list plus "Other" for any OpenRouter model id. The running model still comes from `LLM_MODEL`.
- **Consequences:** The list must be checked against openrouter.ai/models before each release. The per-run model
  picker on the Screen page (baseline task 5.2) stays deferred.

### D-026 - The candidate side has no accounts; an access code opens a profile (2026-11-09, proposed)
- **Context:** Accounts and permissions are out of scope for the MVP (SOW section 6).
- **Decision:** Creating a profile shows a random access code once; only its SHA-256 hash is stored.
- **Consequences:** A lost code cannot be recovered. Anyone with the code can open the profile, so it must be
  treated like a password. Production needs real accounts (roadmap).

### D-027 - The model writes wording only; code prints the facts (2026-11-09, proposed)
- **Context:** Resume-drafting hallucination is a medium-likelihood, high-impact risk in the SOW register.
- **Decision:** The draft schema lets the model write a summary, a skills list, and bullets under experience
  facts. Job titles, employers, schools, degrees, certifications, and dates are rendered from the facts.
- **Consequences:** The model cannot invent an employer or a degree, by construction. The guard only has to
  check free text. Candidates cannot hand-edit a draft in the MVP: they change their facts and draft again.

### D-028 - The guard is strict: named terms and numbers exact, posting words blocked (2026-11-09, proposed)
- **Decision:** See `docs/drafting_guard.md`. A word that comes from the job posting and is not supported by the
  facts blocks the draft, even an ordinary word.
- **Consequences:** Some honest rewordings are rejected. That is the safe direction, and the fallback (D-029)
  means the candidate always gets a usable draft.

### D-029 - Fallback is the candidate's own wording (2026-11-09, proposed)
- **Decision:** After two guard failures or an AI outage, the assistant arranges the candidate's fact lines by
  relevance to the posting, with no AI wording. This is the risk register's "template drafting with fact slots".
- **Consequences:** Drafting works with no API key, which also makes the demo safe offline.

### D-030 - Candidate prompts and drafts are not written to the audit log (2026-11-09, proposed)
- **Context:** The audit log, with its raw prompt viewer, is an employer-side page. Candidate prompts contain the
  candidate's facts.
- **Decision:** Candidate events record what happened (source, guard result, model, hashes, counts) and never
  the content. This extends D-015.

### D-031 - A resume with no masked name is not sent to the model (2026-11-09, proposed)
- **Context:** The prototype audit found header layouts where the name was never found, so the whole resume went
  to the model and the audit log unmasked.
- **Decision:** Masking was hardened, and screening now refuses to send a readable resume that has no `[NAME]`
  in its masked text. It goes to Needs review with that reason. `REQUIRE_NAME_MASK=false` turns this off.
- **Consequences:** A few resumes need a human read. Resumes ingested before this change keep their old masked
  text: re-import them (or reseed the demo) to apply the new rules.

### D-032 - Schema changes are applied in place at startup (2026-11-09, proposed)
- **Decision:** `db.upgrade_schema` adds columns the models define and an older SQLite file lacks. It never drops
  or rewrites anything. File databases use WAL mode and a five-second busy timeout.
- **Consequences:** No migration tool to learn for the MVP. A change that needs more than "add a column" will
  need a real migration tool.

### D-033 - Sample postings carry all four categories; demo thresholds lowered to 35 (2026-11-09, proposed)
- **Decision:** Both sample postings gained education, experience, and certification lines, and the sample roles
  ship with unequal weights. Six fictional resumes were added (26 in total). The sample roles' auto-reject
  threshold moved from 40 to 35 so the documented shortlists and borderline groups stay the same.

### D-034 - A/B scoring reuses the employer engine on the candidate's own posting (2026-11-09, proposed)
- **Context:** The SOW promises "A/B testing of resume versions against a role" and says candidates have no
  access to employer data. The sprint plan had deferred this (baseline task 6.4); Grant asked for it to be built.
- **Decision:** The candidate pastes a job description. It becomes criteria once (proxy-flagged criteria are
  dropped), and each chosen version is scored on those criteria with the screening prompt and
  `compute_fit_score`, using equal category weights. Nothing is read from or written to employer tables, and the
  results are not stored.
- **Consequences:** The score is an estimate: a real employer has their own criteria and weights, and the page
  says so. Each comparison costs one call for the criteria plus one per version. Because results are not stored,
  a page reload in a new browser session loses them.

### D-035 - Suggestions are written by code, not by the model (2026-11-09, proposed)
- **Context:** The SOW requires suggestions "with no added skills, credentials, or experience". The risk register's
  fallback for baseline task 6.5 was a weakest-criteria list.
- **Decision:** For each criterion scored 0-2, code looks for the candidate's own fact lines that share at least
  half of the criterion's key terms, and says one of three things: use a fact this version left out, add real
  detail to a fact already shown, or "nothing in your core facts shows this".
- **Consequences:** A suggestion cannot invent anything, so it needs no guard. Matching is by keywords, so it can
  miss a fact worded very differently from the posting and report a gap that the candidate can close by
  rewording a fact. It never suggests wording.
