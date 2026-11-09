# TalentSift MVP: build plan

**Bottom line:** a local-first Streamlit app that turns a job posting into an approved rubric, screens a folder of
fictional PDF resumes with an LLM via OpenRouter, and produces a ranked, explained, fully auditable shortlist.
Low scorers are auto-rejected with job-related reasons, and every automated decision is logged and reversible.

This plan was proposed before any code was written (Build sequence step 0). The work breakdown structure (WBS)
numbers below are the branch names the team uses going forward (for example `wbs-4.3-scoring-engine`).

## Work breakdown structure

| WBS | Work package | Main files | Owner |
|-----|--------------|------------|-------|
| 1.1 | Scaffold, config, settings | `talentsift/config.py`, `requirements.txt`, `.env.example` | AI owner |
| 1.2 | Data model and DB init | `talentsift/models.py`, `talentsift/db.py` | Data owner |
| 1.3 | Repo hygiene and CI | `.github/`, `CONTRIBUTING.md`, `CHANGELOG.md` | UX owner |
| 1.4 | Docs skeleton | `docs/` | All |
| 2.1 | PDF parser | `talentsift/parsers/pdf.py` | Data owner |
| 2.2 | Parser registry and DOCX stub | `talentsift/parsers/registry.py`, `docx.py`, `base.py` | Data owner |
| 2.3 | PII masking | `talentsift/masking.py` | Data owner |
| 2.4 | Resume ingestion | `talentsift/ingest.py` | Data owner |
| 3.1 | LLM interface | `talentsift/llm/base.py`, `talentsift/schemas.py` | AI owner |
| 3.2 | OpenRouter client | `talentsift/llm/openrouter_client.py` | AI owner |
| 3.3 | Fake offline client | `talentsift/llm/fake_client.py` | AI owner |
| 4.1 | Rubric drafting and role versioning | `talentsift/rubric.py`, `talentsift/roles.py`, `prompts/rubric_draft_v1.md` | AI owner |
| 4.2 | Evidence verification | `talentsift/scoring.py` | AI owner |
| 4.3 | Scoring engine | `talentsift/scoring.py`, `prompts/screen_v1.md` | AI owner |
| 4.4 | Status, ranking, auto-reject | `talentsift/scoring.py` | AI owner |
| 4.5 | Audit log, overrides, fairness checks | `talentsift/audit.py`, `overrides.py`, `fairness.py` | AI owner / Data owner |
| 5.1 | Roles page | `pages/1_Roles.py` | UX owner |
| 5.2 | Applicants page | `pages/2_Applicants.py` | Data owner |
| 5.3 | Screen page | `pages/3_Screen.py` | UX owner |
| 5.4 | Results page | `pages/4_Results.py` | UX owner |
| 5.5 | Audit page | `pages/5_Audit.py` | Data owner |
| 6.1 | Sample resumes and role profiles | `scripts/make_sample_resumes.py` | Data owner |
| 6.2 | Seed script and demo loader | `talentsift/demo.py`, `scripts/seed_demo.py` | Data owner |
| 6.3 | End-to-end demo runs | `tests/test_end_to_end.py` | All |
| 7.1 | README and release notes | `README.md`, `CHANGELOG.md` | All |
| 7.2 | Requirements traceability | `docs/traceability.md` | All |

## File tree

```
app.py                          # Streamlit entry: banner, status, demo loader
pages/
  1_Roles.py                    # posting -> AI-drafted rubric -> edit/approve; thresholds; versions
  2_Applicants.py               # upload / folder import, parse status, masked preview toggle
  3_Screen.py                   # pick role + batch, run, progress, token and cost tally
  4_Results.py                  # Shortlist | Not shortlisted | Auto-rejected | Needs review
  5_Audit.py                    # audit log, prompt/response viewer, CSV export, fairness checks
talentsift/
  config.py                     # settings from env / .env / Streamlit secrets
  db.py                         # engine, schema creation, append-only triggers
  models.py                     # SQLModel tables
  schemas.py                    # Pydantic models for LLM I/O + semantic validators
  parsers/base.py               # ResumeParser protocol, ParsedResume
  parsers/pdf.py                # PdfParser (pdfplumber)
  parsers/docx.py               # DocxParser stub (planned for final project)
  parsers/registry.py           # choose a parser by file extension
  masking.py                    # deterministic PII masking, no AI
  ingest.py                     # file -> parse -> mask -> Applicant row + audit events
  llm/base.py                   # LLMClient protocol, LLMResult, strict schema, validation loop
  llm/openrouter_client.py      # OpenAI SDK pointed at OpenRouter
  llm/fake_client.py            # deterministic offline client (tests + demo backup)
  llm/factory.py                # build the configured client
  prompts.py                    # load versioned prompt files
  rubric.py                     # draft criteria from a pasted posting
  roles.py                      # create, edit, duplicate, version, approve roles
  scoring.py                    # screening run: prompt, cache, validate, verify, score, rank, statuses
  overrides.py                  # human overrides, reinstatements, confirm-mode approvals
  fairness.py                   # consistency re-run and name-swap checks
  audit.py                      # append-only event writer, queries, CSV export
  demo.py                       # seed sample roles + resumes (used by script and app)
  ui.py                         # shared Streamlit helpers (banner, DB, client picker)
prompts/screen_v1.md            # versioned screening prompt
prompts/rubric_draft_v1.md      # versioned rubric drafting prompt
scripts/make_sample_resumes.py  # 20 fictional PDF resumes + 2 role profiles
scripts/seed_demo.py            # load sample roles and resumes (re-run after a hosted reboot)
data/resumes/samples/           # committed fictional resumes; everything else in data/ is gitignored
data/samples/roles/             # role profiles (job postings)
docs/                           # plan, requirements, traceability, decisions, architecture
tests/                          # pytest suite, run in CI on every pull request
```

## Build sequence and exit criteria

| Step | Scope | Exit criterion |
|------|-------|----------------|
| 1 | Scaffold, config, models, DB init, docs skeleton, CI | DB initializes; append-only triggers proven by tests |
| 2 | Parsers, masking, ingestion | Parser and masking tests green |
| 3 | LLM interface, OpenRouter client, fake client | Request shape and fallback proven with mocked HTTP |
| 4 | Rubric, scoring, evidence, statuses, auto-reject, audit | Score math, guardrails, determinism tests green |
| 5 | Streamlit pages | Every page renders in headless smoke tests |
| 6 | Sample data, seed, full demo screens | Two roles rank the same folder differently |
| 7 | README, traceability, changelog | Definition of done checklist satisfied |

## Key risks and mitigations

| Risk | Mitigation |
|------|------------|
| LLM fabricates evidence | Every quote verified against masked text; unverified evidence blocks auto-reject |
| Auto-reject harms a good applicant | Guardrails route uncertain cases to review; one-click reinstate; `confirm` mode |
| Model or provider drift | Model slug in env only; prompt versions recorded; results cached per model and prompt |
| Runaway cost on 200 applicants | Per-batch cost cap stops the run cleanly; cache makes re-runs free |
| Demo-day network failure | Fake offline client produces deterministic, explainable results |
| Hosted demo loses SQLite file on reboot | `AUTO_SEED_DEMO` and a "Load demo data" button rebuild the demo in seconds |

## MVP build sprint (November 2026)

The v1 prototype above became the MVP in three short sprints (Nov 4 to Nov 17, present Nov 18). The backlog is
the 27 Mission Control tasks; each has a GitHub issue. Seven baseline tasks are deferred past Nov 18: 1.2, 4.3
DOCX parser, 4.5 reliability pass, 5.2 per-run model picker, 6.4 A/B scoring, 6.5 improvement suggestions, 6.7
offline client extensions.

| Task | WBS | Work package | Owner | Sprint | Issue | Main files or document |
|------|-----|--------------|-------|--------|-------|------------------------|
| T01 | 1.1 | Kickoff, sprint charter, role assignment | Grant | 1 | #3 | `docs/decisions.md` |
| T03 | 2.1 | Prototype audit and defect backlog | Damian | 1 | #4 | `docs/prototype_audit.md` |
| T04 | 2.2 | Candidate-side requirements | Arizbeth | 1 | #5 | `docs/requirements.md` FR-34 to FR-41 |
| T05 | 2.3 | Weighting and model-selection requirements | Grant | 1 | #6 | `docs/requirements.md` FR-29 to FR-33 |
| T06 | 2.4 | Deployment memo | Damian | 1 | #7 | `docs/deployment_memo.md` |
| T07 | 2.5 | Requirements sign-off (M1) | Grant | 1 | #8 | decision log |
| T08 | 3.1 | Data model changes | Damian | 1 | #9 | `models.py`, `db.py::upgrade_schema` |
| T09 | 3.2 | Candidate UX wireframes | Arizbeth | 1 | #10 | `docs/candidate_wireframes.md` |
| T10 | 3.3 | Prompt design and no-new-facts guard spec | Grant | 1 | #11 | `docs/drafting_guard.md`, `prompts/resume_draft_v1.md` |
| T11 | 3.4 | Employer upgrade design | Arizbeth | 2 | #12 | `docs/employer_upgrade_design.md`, `model_catalog.py` |
| T12 | 3.5 | Design approval (M2) | Grant | 2 | #13 | decision log |
| T13 | 4.1 | Defect fixes from the audit | Damian | 2 | #14 | `masking.py`, `ingest.py`, `job_import.py`, `fairness.py` |
| T14 | 4.2 | Persistence and backup | Damian | 2 | #15 | `backup.py`, `scripts/backup.py`, `config.py` |
| T16 | 4.4 | Masking hardening | Damian | 2 | #16 | `masking.py`, `scoring.py::_name_exposed` |
| T18 | 5.1 | Category weights | Grant | 2 | #17 | `scoring.py`, `roles.py`, `pages/1_Roles.py`, `pages/4_Results.py` |
| T20 | 6.1 | Core-facts profile | Damian | 2 | #18 | `candidates.py` |
| T21 | 6.2 | Drafting assistant with guard | Grant | 2 | #19 | `guard.py`, `drafting.py`, `draft_template.py` |
| T22 | 6.3 | Version save and compare | Damian | 2 | #20 | `drafting.py` |
| T25 | 6.6 | Candidate pages | Arizbeth | 3 | #21 | `pages/7_`, `8_`, `9_Candidate_*.py` |
| T27 | 7.1 | Unit tests for new modules | Arizbeth | 3 | #22 | `tests/` |
| T28 | 7.2 | Integration and end-to-end run | Damian | 3 | #23 | `tests/test_end_to_end.py` |
| T29 | 7.3 | Fairness and guardrail validation | Damian | 3 | #24 | `docs/validation_report.md` |
| T30 | 7.4 | UAT with pilot users | Arizbeth | 3 | #25 | `docs/uat_script.md` |
| T31 | 7.5 | Defect fix cycle (M5) | Grant | 3 | #26 | issues |
| T32 | 8.1 | Documentation update | Grant | 3 | #27 | `README.md`, `docs/install.md` |
| T33 | 8.2 | MVP deployment | Damian | 3 | #28 | `docs/install.md`, `deploy/` |
| T34 | 8.3 | Presentation and demo package (M6) | Grant | 3 | #29 | `docs/demo_script.md` |

New modules since v1:

```
talentsift/model_catalog.py     # the short list of models an employer can choose
talentsift/backup.py            # database + uploads archive, rotate, verify, restore
talentsift/candidates.py        # candidate profile, consent, access code, core facts
talentsift/draft_template.py    # fact-only draft builder: the fallback and the offline "model"
talentsift/guard.py             # the no-new-facts guard (deterministic, no AI)
talentsift/drafting.py          # prompt, guarded retry, fallback, rendering, saved versions, compare
talentsift/textutil.py          # shared keyword and stemming helpers
pages/7_Candidate_Profile.py    # notice and consent, core facts, delete my data
pages/8_Candidate_Draft.py      # draft for a pasted job description, check result, approve and save
pages/9_Candidate_Versions.py   # saved versions and side-by-side compare
prompts/rubric_draft_v2.md      # adds a category per criterion
prompts/resume_draft_v1.md      # fact-anchored drafting
scripts/backup.py               # backup CLI for cron
deploy/talentsift.service       # systemd unit for a self-hosted install
```

