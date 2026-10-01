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
