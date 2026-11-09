# Candidate UX wireframes (T09, WBS 3.2)

**Bottom line:** three pages, one direction: profile, draft, versions. The candidate sees the AI notice before
anything else, sees the check result on every draft, and must tick an approval before anything is saved.

Flow: **Notice and consent -> Core facts -> Paste a job description -> Draft + check result -> Approve and save
-> Compare versions.** Employer pages and data are never linked from these pages.

## 1. Candidate Profile (no profile open)

```
+--------------------------------------------------------------------------------+
| [shield] Drafts use only the facts you enter. Software checks every line...    |
| Candidate profile                                                              |
| [ Start a profile ] [ Return with your access code ]                           |
| +----------------------------------------------------------------------------+ |
| | Before you start: how TalentSift uses AI and your information              | |
| |  - AI drafting: facts + job description go to the model; name/contact don't| |
| |  - Nothing is added; you approve every draft                               | |
| |  - AI screening by employers; names, addresses, ZIP codes removed          | |
| |  - Your data is separate; delete it any time                               | |
| +----------------------------------------------------------------------------+ |
| Name [______________________]   Headline [______________________]              |
| Email [________]  Phone [________]  City and state [________]                  |
| [ ] I have read the notice above and agree to AI-assisted drafting...          |
| ( Create my profile )                                                          |
+--------------------------------------------------------------------------------+
```

After creation: a one-time box with the access code and an "I have saved my code" button.

## 2. Candidate Profile (profile open)

```
| > Name and contact details  (never sent to the AI model)                       |
| Core facts (5)                                                                 |
| [ Skills (1) ] [ Education (1) ] [ Certifications (1) ] [ Experience (2) ]     |
|  > Data Analyst · Fictional Grocers · 2021 – 2025                              |
|       Job title [__________]  Employer [__________]                            |
|       Start [____]  End [____]                                                 |
|       What you did (one accomplishment per line) [________________________]    |
|       ( Save changes )   ( Remove this fact )                                  |
|  Add experience:  ...same fields...  ( Add to experience )                     |
| ------------------------------------------------------------------------------ |
| [ Next: draft a resume for a role ]        ( Close my profile on this device ) |
| > Your data and consent   (notice text, consent date, Delete everything)       |
```

## 3. Candidate Draft

```
| Draft a resume                                                                 |
| Role you are applying for [______________________]                             |
| Job description [.....paste the posting.....]                                  |
| ( Draft from my 5 facts )                                                      |
|                                                                                |
| Your draft                                                                     |
| [shield] Draft ready. Every line was checked against your core facts.          |
| No-new-facts check: Passed | Lines checked: 12 | Wording: AI, from your facts  |
| > What the check rejected before this draft (6)                                |
| > Wording to double-check (1)                                                  |
| +----------------------------------------------------------------------------+ |
| | # Avery Exampleton                                                         | |
| | ## Summary ...  ## Skills ...  ## Experience ...  ## Education ...         | |
| +----------------------------------------------------------------------------+ |
| ( Download as text )                                                           |
| Approve and save this version                                                  |
| Name this version [__________]                                                 |
| [ ] I have read this draft, and everything in it is accurate.                  |
| ( Approve and save )   ( Discard this draft )                                  |
```

If the AI's wording failed the check twice, the banner turns amber and says the draft uses the candidate's own
wording instead. The approval box is empty again for every new draft.

## 4. Candidate Versions

```
| Saved versions                                                                 |
| Version | Target role | Wording | Skills | Bullets | Words | Saved             |
| Compare two versions:  Version A [v]   Version B [v]                           |
|   3 line(s) only in A · 4 line(s) only in B · skills only in B: Power BI       |
|   +----------------------------+  +----------------------------+               |
|   | version A text             |  | version B text             |               |
|   +----------------------------+  +----------------------------+               |
|   > Line-by-line differences                                                   |
| Score versions against a role (A/B)                                            |
|   Versions [v v]   Role [________]   Job description [..........]              |
|   ( Score these versions )                                                     |
|   [flag] 'Analyst' scores best for this role: 84.8 out of 100.                 |
|   Version | Fit score | Skills | Education | Certifications | Experience       |
|   > Scores by criterion                                                        |
|   Suggestions                                                                  |
|   > Analyst: 2 suggestion(s)   (in your facts but left out / thin / not in     |
|                                 your core facts)                               |
| Open a version [v]   ( Download as text )   ( Delete 'name' )                  |
```

## Design choices to confirm at the design review (T12)

1. **No free editing of a draft.** To change wording, the candidate changes a fact and drafts again. This keeps
   every saved line covered by the check. Is that acceptable for UAT?
2. **Access code instead of accounts.** Shown once, cannot be recovered.
3. **The AI provider switch is visible on candidate pages** (same sidebar as the employer side). Fine for a
   demo; a hosted product would hide it.
4. **A/B scores are an estimate.** The Versions page scores versions against a pasted job description with equal
   category weights and says so. It also lists suggestions drawn from the candidate's own facts.
