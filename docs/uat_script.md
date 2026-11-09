# UAT script: pilot users (T30, WBS 7.4)

**Goal:** confirm that a hiring manager can produce a ranked shortlist with no training, and that a candidate
trusts and understands the drafting check. Two scenarios, about 20 minutes each. Planned for November 16.

**Rules:** fictional sample resumes only, unless a participant brings their own resume and consents in writing.
Delete participant data at closeout. Read the consent line aloud before each session:

> "TalentSift uses AI to score resumes and to suggest resume wording. Today you will use sample data. We will
> take notes on what you do and say, with no names attached. You can stop at any time."

Participants: 2 or 3 per scenario. Classmates stand in if pilot users are not confirmed by November 11.
Facilitator: Arizbeth. Note-taker: one other founder. Do not help unless the participant is stuck for a minute.

## Scenario A: hiring manager

Setup: demo data loaded, a real model configured, the "Business Data Analyst" role in draft.

| # | Ask the participant to | Pass if | Result / notes |
|---|------------------------|---------|----------------|
| A1 | Open the Business Data Analyst role and say what the AI will look for | They find the criteria and read at least one aloud | |
| A2 | Make experience count twice as much as education, then save and approve | Weights changed, version approved, without help | |
| A3 | Screen the sample batch | Run finishes; they can say how many were screened | |
| A4 | Tell us who the top candidate is and why | They cite the score and at least one evidence quote or category | |
| A5 | Explain why the second candidate scored lower than the first | They open the score breakdown and point to a category | |
| A6 | Find an auto-rejected applicant, say why, and bring them back | Reason read aloud; reinstated with a typed reason | |
| A7 | Tell us what the AI knew about the candidates' names and addresses | They say it was hidden, or find the masked preview | |

Time from A3 start to a shortlist on screen: ______ (target: under five minutes for 26 resumes).

## Scenario B: candidate

Setup: no profile. Give the participant the fictional fact sheet below, or let them use their own background.

> Data Analyst at Fictional Grocers, 2021 to 2025. Built 12 Tableau dashboards for the finance team. Wrote SQL
> queries to analyze weekly sales. Skills: SQL, Tableau, Excel. B.S. Business Analytics, Example State
> University, 2019.

| # | Ask the participant to | Pass if | Result / notes |
|---|------------------------|---------|----------------|
| B1 | Start a profile | They read (or skim) the notice, tick consent, and save their access code | |
| B2 | Enter the facts from the sheet | At least one skill, one job with details, one degree | |
| B3 | Draft a resume for the sample analyst posting (it asks for Snowflake, which the sheet lacks) | A draft appears with "Passed" | |
| B4 | Tell us whether the draft says they know Snowflake, and why not | They say no, and connect it to "only my facts" | |
| B5 | Save the draft | They tick the approval and name the version | |
| B6 | Add "Power BI" as a skill, draft again for a second posting, save, and compare the two | Two versions; they find a difference | |
| B7 | Tell us how they would remove all their data | They find "Delete my profile and all my data" | |

## After each session

Ask, and write down the answers word for word:

1. What was confusing?
2. Would you trust this ranking (A) / this draft (B)? What would make you trust it more?
3. What is missing that you expected?

## Results and sign-off

| Scenario | Participants | Tasks passed | Blocking defects (issue links) |
|----------|--------------|--------------|--------------------------------|
| A | | / 7 | |
| B | | / 7 | |

Log every defect as a GitHub issue with the `bug` label and link it from T31. Sign-off (M5): all three founders,
date: ____________
