# Contributing

Three people maintain TalentSift. These rules keep parallel work safe and leave a clear process trail for the course.

## Ownership

| Owner | Areas |
|-------|-------|
| Data owner | `talentsift/parsers/`, `masking.py`, `ingest.py`, `demo.py`, `pages/2_Applicants.py`, `pages/5_Audit.py`, `scripts/` |
| AI owner | `talentsift/llm/`, `schemas.py`, `prompts.py`, `prompts/`, `rubric.py`, `scoring.py`, `audit.py`, `overrides.py`, `fairness.py` |
| UX owner | `app.py`, `talentsift/ui.py`, `roles.py`, `pages/1_Roles.py`, `pages/3_Screen.py`, `pages/4_Results.py` |

Anyone can change anything, but the owner reviews the pull request.

## Workflow

1. Pick or open an issue using the **WBS task** template (see `docs/plan.md` for WBS ids).
2. Branch from `main`: `git checkout -b wbs-4.3-scoring-engine`.
3. Commit with [Conventional Commits](https://www.conventionalcommits.org/):
   `feat(scoring): add must-have penalty`, `fix(masking): keep ASP.NET intact`, `docs: update traceability`,
   `test(audit): cover reinstatement`, `chore(ci): cache pip`.
4. Run `pytest` locally. Add a test for every behavior change.
5. Open a pull request using the template. CI must be green before merge.
6. Update `CHANGELOG.md` (under "Unreleased"), `docs/traceability.md`, and `docs/decisions.md` when relevant.

## Ground rules

- **Fictional data only.** Never commit real resumes or applicant information. `data/` is gitignored except the
  generated samples.
- **No secrets in git.** Keys live in `.env` (local) or the Streamlit secrets manager (hosted).
- **The audit log is append-only.** Never add code that updates or deletes `AuditEvent` or `Override` rows; a test
  scans the codebase for it.
- **Scores are computed in code.** Prompt changes need a new prompt version (`prompts/screen_v2.md`), not an edit
  in place.
- **Keep the LLM blind to identity.** Only `masked_text` and `display_label` may reach a prompt.

## Local setup

See the README. Quick version:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add OPENROUTER_API_KEY and LLM_MODEL, or set LLM_PROVIDER=fake
pytest
streamlit run app.py
```
