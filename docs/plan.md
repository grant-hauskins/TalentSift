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
