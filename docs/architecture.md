# Architecture

## Context diagram

TalentSift is a single Streamlit application with a local SQLite database. The only external system is the LLM
provider, reached through OpenRouter. Resumes are read from uploads or a local folder.

```mermaid
flowchart LR
    HM([Hiring manager])
    FILES[(Resume files<br/>PDF upload or folder)]
    OR[[OpenRouter API]]
    LLM[[Model providers<br/>chosen by LLM_MODEL]]

    subgraph TS[TalentSift]
        UI[Streamlit pages]
        CORE[talentsift package]
        DB[(SQLite)]
    end

    HM -- "posting, rubric edits, approvals, overrides" --> UI
    UI -- "shortlist, reasons, audit log, CSV" --> HM
    FILES -- "PDF bytes" --> CORE
    UI <--> CORE
    CORE <--> DB
    CORE -- "masked resume + rubric (JSON schema)" --> OR
    OR -- "scores, rationales, quotes, usage, cost" --> CORE
    OR <--> LLM
```

Only masked text and a display label such as "Applicant 07" ever leave the machine.

## Level-0 data flow diagram

```mermaid
flowchart TB
    HM([Hiring manager])
    LLM[[LLM via OpenRouter]]
    FOLDER([Resume files])

    P1((1.0<br/>Define role))
    P2((2.0<br/>Ingest and mask resumes))
    P3((3.0<br/>Screen applicants))
    P4((4.0<br/>Rank and decide status))
    P5((5.0<br/>Review and override))
    P6((6.0<br/>Audit and export))

    D1[(D1 Roles and criteria)]
    D2[(D2 Applicants)]
    D3[(D3 Runs, evaluations, scores)]
    D4[(D4 Audit log, append-only)]

    HM -- "job posting, edits, approval" --> P1
    P1 -- "posting" --> LLM
    LLM -- "draft criteria, proxy flags" --> P1
    P1 -- "versioned rubric" --> D1

    FOLDER -- "PDF files" --> P2
    P2 -- "raw text, masked text, parse status" --> D2

    D1 -- "approved rubric" --> P3
    D2 -- "masked text, display label" --> P3
    P3 -- "prompt" --> LLM
    LLM -- "criterion scores + quotes" --> P3
    P3 -- "verified scores, fit score" --> D3

    D3 -- "fit scores, guardrail flags" --> P4
    P4 -- "rank, status, reasons" --> D3
    P4 -- "shortlist and reasons" --> HM

    HM -- "override or reinstate + reason" --> P5
    P5 -- "new status" --> D3

    P1 & P2 & P3 & P4 & P5 -- "events" --> D4
    D4 --> P6
    HM -- "filters, export request" --> P6
    P6 -- "log view, CSV, fairness checks" --> HM
```

## Module map

| Layer | Modules | Notes |
|-------|---------|-------|
| UI | `app.py`, `pages/*.py`, `talentsift/ui.py` | Thin: pages call package functions and render results |
| Domain services | `roles.py`, `rubric.py`, `ingest.py`, `scoring.py`, `overrides.py`, `fairness.py`, `demo.py` | All business rules live here and are unit tested |
| AI | `llm/base.py`, `llm/openrouter_client.py`, `llm/fake_client.py`, `llm/factory.py`, `schemas.py`, `prompts.py` | Provider-agnostic `LLMClient`; no database access from the LLM layer |
| Data | `models.py`, `db.py`, `audit.py`, `parsers/*`, `masking.py` | SQLite via SQLModel; append-only audit and override tables |

## Screening sequence

```mermaid
sequenceDiagram
    participant M as Manager
    participant S as scoring.run_screening
    participant C as LLMClient
    participant DB as SQLite

    M->>S: role (approved version) + applicants
    S->>DB: ScreeningRun + run_started event
    loop each screenable applicant (parallel LLM calls, ordered writes)
        S->>DB: cache lookup by hash(masked text, role version, prompt, model)
        alt cache miss
            S->>C: complete_json(system, user, schema)
            C-->>S: JSON + model, provider, tokens, cost
            S->>S: validate (retry once), verify quotes
        end
        S->>DB: Evaluation + CriterionScores + llm_*/score_computed events
    end
    S->>S: rank, apply threshold, top N, guardrails
    S->>DB: statuses, reasons, ranking_computed, auto_reject events
    S-->>M: shortlist, not shortlisted, auto-rejected, needs review
```

## Key design rules

1. **The LLM scores criteria; code decides.** Fit score, ranking, and status are computed in code from 0-4 criterion scores.
2. **Evidence or it did not happen.** Every quote is checked against the masked text before it can support a decision.
3. **Uncertainty goes to a human.** Validation failures, unverified evidence, partial parses, truncated input, and unscored applicants go to `needs_review`, never to auto-reject.
4. **Append-only history.** Audit events and overrides cannot be updated or deleted (SQLite triggers plus an ORM guard).
5. **Swap, don't rewrite.** New models change one env var; new file formats add one parser class.

## Candidate side (added in the MVP build)

The candidate side is a second set of pages in the same app. It shares the LLM layer and the four scoring
categories with the employer side and nothing else: candidate tables have no foreign key to employer tables, and
candidate pages never query them.

```mermaid
flowchart LR
    C([Candidate])
    LLM[[LLM via OpenRouter]]

    P7((7.0<br/>Profile and consent))
    P8((8.0<br/>Draft for a role))
    G{{No-new-facts guard<br/>code, no AI}}
    T[Fact template<br/>fallback]
    P9((9.0<br/>Approve, save, compare))

    D5[(D5 Candidate profiles<br/>and core facts)]
    D6[(D6 Resume versions)]
    D4[(D4 Audit log<br/>ids, counts, hashes only)]

    C -- "consent, core facts" --> P7 --> D5
    C -- "job description" --> P8
    D5 -- "facts (no name or contact details)" --> P8
    P8 -- "facts + job description" --> LLM
    LLM -- "summary, skills, bullets" --> G
    G -- "fails twice, or AI down" --> T --> G
    G -- "passed draft" --> P9
    C -- "approval" --> P9 --> D6
    P8 -- "source, guard result" --> D4
```

Titles, employers, schools, and dates in a draft are printed from D5 by code. The model's output schema has no
field for them. Details: [drafting_guard.md](drafting_guard.md).

## Fit score with category weights

```mermaid
flowchart LR
    S[0-4 criterion scores<br/>from the model] --> CS[Category score<br/>weighted average, 0-100]
    W[Employer weights<br/>skills, education,<br/>certifications, experience] --> SH[Shares of the<br/>categories in use]
    CS --> F[Fit score = sum of share x score<br/>minus must-have penalty]
    SH --> F
    F --> B[Breakdown stored on<br/>every evaluation]
```

