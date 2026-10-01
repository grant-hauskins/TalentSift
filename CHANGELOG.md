# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-01 (MVP)

### Added
- **Scaffold** (WBS 1): settings from `.env` or Streamlit secrets (no hardcoded model slug), SQLModel data model,
  SQLite with append-only `audit_event` and `override` tables (triggers plus an ORM guard).
- **Ingestion** (WBS 2): text-based PDF parser with `needs_ocr` and `partial` (scanned page) detection, parser
  registry with a DOCX stub, deterministic PII masking (names, emails, phones, URLs and handles, street addresses
  and ZIP codes, image content, optional graduation years), upload and folder import with duplicate detection.
- **LLM layer** (WBS 3): provider-agnostic `LLMClient`; OpenRouter client on the `openai` SDK with strict JSON
  schema, `provider.require_parameters`, plain-JSON fallback, and one network retry; deterministic offline client;
  versioned prompts `screen_v1` and `rubric_draft_v1`.
- **Screening engine** (WBS 4): AI-drafted rubrics with proxy flags; immutable approved rubric versions; result
  cache; parallel AI calls; validate-and-retry; verbatim evidence verification; fit score with must-have penalty;
  deterministic ranking; statuses with auto-reject guardrails and `confirm` mode; job-related reasons; per-batch
  cost cap; overrides and one-click reinstatement with required reasons; consistency and name-swap checks.
- **UI** (WBS 5): Home, Roles, Applicants, Screen, Results, and Audit pages. Every page shows the disclaimer banner
  and the AI provider picker (OpenRouter or offline), even when opened directly by URL (D-021).
- **Demo** (WBS 6): 20 fictional PDF resumes with planted strong, borderline, and weak fits for two roles, a
  name-swap pair, a partially scanned resume, and an image-only resume; `scripts/seed_demo.py`, a "Load demo data"
  button, and `AUTO_SEED_DEMO` for hosted demos.
- **Process** (WBS 1 and 7): plan and WBS, requirements, traceability, decision log, architecture diagrams,
  contributing guide, issue and PR templates, CI running pytest on every pull request, README.
