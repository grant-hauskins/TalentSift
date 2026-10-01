## What and why

<!-- One or two sentences. Link the WBS task and issue, e.g. "WBS 4.3, closes #12". -->

## Requirements touched

<!-- e.g. FR-13, NFR-2. Update docs/traceability.md if this PR adds or moves a requirement. -->

## How I tested it

- [ ] `pytest` passes locally
- [ ] New or changed behavior has a test
- [ ] Ran the app with the fake client (`LLM_PROVIDER=fake streamlit run app.py`) if UI changed

## Checklist

- [ ] Branch is named `wbs-<id>-<slug>`
- [ ] Commit messages follow Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`)
- [ ] `CHANGELOG.md` updated under "Unreleased" for user-visible changes
- [ ] `docs/decisions.md` has an entry for any design decision
- [ ] No real applicant data, API keys, or `.env` files committed
