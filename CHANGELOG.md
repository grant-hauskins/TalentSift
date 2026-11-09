# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Category weights.** Every criterion belongs to skills, education, certifications, or experience. The manager
  weights the four categories on the Roles page; the fit score combines category scores by those weights.
- **Score breakdown** by category for every candidate on Results; each run records the weights it used.
- **Model list** on Settings with Claude as the default, plus any other OpenRouter model id.
- **Candidate side**: profile with AI notice and consent, core facts, a fact-anchored drafting assistant with a
  no-new-facts guard, approve-and-save versions, and side-by-side compare. Three new pages.
- **Backups**: `scripts/backup.py` (snapshot, rotate, verify, restore) and `TALENTSIFT_DATA_DIR`.
- **Schema upgrade in place**: an existing database gains new columns on start.
- Six more fictional sample resumes (26 in total); sample postings now use all four categories.
- `ALLOW_FOLDER_IMPORT` and `IMPORT_ROOT` for shared installs; `REQUIRE_NAME_MASK`.

### Fixed
- **Masking**: names, street addresses, and ZIP codes survived in common header layouts (name with a phone or
  email on the same line, "Last, First", ALL-CAPS addresses, full state names, bare ZIP codes, and more). See
  `docs/prototype_audit.md`. A resume whose name cannot be masked is no longer sent to the model.
- Masking no longer hides job text such as "15 Court filings", "EN 50128", "socket.io", or "@Google".
- Empty or non-PDF uploads no longer become applicants.
- The consistency check now re-scores with the run's own penalty and model.
- Job-link import no longer shows a traceback on malformed links.
- The folder browser no longer crashes on `~user` or an empty path.

### Changed
- Rubric drafting uses `rubric_draft_v2` (adds a category per criterion).
- Sample roles ship with category weights and an auto-reject threshold of 35.
- File databases use write-ahead logging and a busy timeout, for a few concurrent users.

- **Settings page**: enter and store the OpenRouter API key and model in the app. Saved to the gitignored `.env`,
  applied without a restart, shown masked, never written to the database or audit log. The sidebar links to it
  when a key is missing.
- **Folder browser** on Applicants: type a path or click through folders (Up, Home, Samples, subfolders). Before
  importing, a preview shows which files will be sent, which are already imported, and which are ignored and why.
- **Job posting import from a link** on Roles: JSON-LD `JobPosting` first, then BeautifulSoup heuristics (named
  container, `<main>`/`<article>`, densest text block). Public http(s) hosts only; size and time limits; each import
  logged as `posting_imported`.

### Changed
- Folder import and upload accept **PDF files only**. Files must have a `.pdf` name *and* PDF contents; hidden
  files, empty files, and renamed non-PDFs are skipped with a reason. Subfolders are not read.
- `httpx` and `beautifulsoup4` are now runtime dependencies.

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
