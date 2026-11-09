# Demo script: final presentation (T34, WBS 8.3)

**Goal:** show the two SOW success criteria live in about eight minutes: rank at least 25 resumes with a score
breakdown for every candidate, and demonstrate both sides. Presentation: November 18. Rehearse by November 17.

## Before you present

| Check | How |
|-------|-----|
| Fresh demo data | `python scripts/seed_demo.py --reset` (roles and 26 resumes, no runs yet) |
| Real model ready | Settings shows a stored key and the model; run one resume to warm up |
| Offline fallback ready | Know where the sidebar switch is: "Offline fake client" |
| Candidate profile ready | Create "Avery Exampleton" with the fact sheet in `docs/uat_script.md`; keep the access code |
| Spend | Check the OpenRouter balance against the $50 cap |
| Screen | Browser zoom 125%, sidebar open, one tab per side |

## Part 1: employer side (about 4 minutes)

| Step | Do | Say |
|------|----|-----|
| 1 | Roles: open Business Data Analyst | "The manager pastes a posting. The AI drafts the criteria; a person approves them." |
| 2 | Point at the four category weights; change Experience from 20 to 40, save, approve | "The employer decides what matters. Changing a weight creates a new version, so every past result stays tied to the weights that produced it." |
| 3 | Applicants: toggle the masked preview on one resume | "Names, street addresses, and ZIP codes are removed in code before the AI sees anything. Illinois law bars ZIP codes as a proxy." |
| 4 | Screen: run all 26 | "One call per resume, temperature zero, with a spending cap." Note the time. |
| 5 | Results: open the top card, then its detail | "Ranked shortlist. Here is the breakdown: category score times the employer's weight. Every quote is checked against the resume." |
| 6 | Auto-rejected tab: open one, reinstate it | "Every automated decision has a job-related reason and can be reversed, with the reason logged." |
| 7 | Audit: show the log for that run | "Everything is logged and append-only." |

## Part 2: candidate side (about 3 minutes)

| Step | Do | Say |
|------|----|-----|
| 8 | Candidate Profile: return with the access code; show the notice and the facts | "The candidate consents first, then enters core facts. These are the only source the assistant may use." |
| 9 | Candidate Draft: paste the analyst posting (it asks for Snowflake) and draft | "The posting decides what goes first. It is never a source of content." |
| 10 | Point at "Passed", then open "What the check rejected" if present | "Software checks every line. No Snowflake: the candidate never said it." |
| 11 | Tick the approval, save as "Analyst" | "Nothing is saved until the candidate approves it." |
| 12 | Candidate Versions: compare with a second saved version, then click "Score these versions" | "Versions side by side, then scored on the same criteria. Same engine as the employer side, on the candidate's own posting." |
| 13 | Open the suggestions for the weaker version | "Suggestions come from the score breakdown and the candidate's own facts. Where the facts do not cover a requirement, it says so. It never writes it for them." |

## Close (about 1 minute)

- Both sides, one scoring vocabulary: skills, education, certifications, experience.
- All eight SOW features are in. Deferred: DOCX, per-run model choice, PDF export, accounts.
- Numbers to quote: batch size and time from step 4; test count from CI.

## If something breaks

| Problem | Do |
|---------|----|
| AI outage or slow network | Sidebar: switch to "Offline fake client" and re-run. Say so: "This is our offline backup." |
| Cost cap stops the run | Re-run: finished resumes come from the cache at no cost |
| The guard rejects the AI's draft twice | Show it: the draft falls back to the candidate's own wording. This is the feature working |
| The app will not start | Run locally: `streamlit run app.py` with the seeded database |

## Closeout (after the presentation)

Delete non-sample resumes and candidate profiles from the demo VM (install guide, step 8), revoke the OpenRouter
key, and file the closeout report in the project workbook.
