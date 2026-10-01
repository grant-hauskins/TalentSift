# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Project scaffold: settings from `.env`, SQLModel data model, SQLite with append-only audit and override tables.
- Docs skeleton: plan and WBS, requirements, decisions, architecture diagrams.
- GitHub issue and pull request templates, contributing guide, CI workflow running pytest on every pull request.
- Text-based PDF parser with `needs_ocr` and `partial` (scanned page) detection; parser registry; DOCX stub.
- Deterministic PII masking: names (first-line heuristic), emails, phones, URLs and handles, street addresses and
  ZIP codes, image content; optional graduation-year masking.
- Resume ingestion from uploads or a folder, with duplicate detection and PII-free audit events.
- Provider-agnostic `LLMClient` with a validate-and-retry-once loop; OpenRouter client (OpenAI SDK, strict JSON
  schema, `require_parameters`, plain-JSON fallback, one network retry); deterministic offline fake client.
- Versioned prompts `screen_v1` and `rubric_draft_v1`.
- AI rubric drafting with proxy-characteristic flags; roles with immutable approved versions, draft editing,
  duplication, discard-draft, and per-role threshold and top N.
- Screening engine: masked-text prompts, result cache, parallel LLM calls, validate-and-retry, verbatim evidence
  verification, fit score with must-have penalty, deterministic ranking, statuses with auto-reject guardrails,
  `confirm` mode, job-related reasons, per-batch cost cap.
- Overrides and one-click reinstatement with required reasons; append-only override records.
- Fairness checks: consistency re-run on a sample and name-swap test.
