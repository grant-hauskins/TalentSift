# Prototype audit and defect backlog (T03, WBS 2.1)

**Bottom line:** the v1 prototype met its own requirements but missed four of the eight SOW features, and it had
one serious defect: names, street addresses, and ZIP codes survived masking in common resume layouts. All gaps
on the Mission Control backlog are closed in this build. Four low-severity defects are deferred, with reasons.

Audit date: November 9, 2026. Method: code read plus reproduction on the offline client. Each defect below was
reproduced before it was fixed, and each fix has a regression test.

## Gap analysis against SOW section 6

| SOW feature | v1 prototype | Now | Task |
|-------------|--------------|-----|------|
| Plain-text role descriptions | Done | Done | n/a |
| Choice of AI model | Free-text model id only | Short list, Claude default, any other id | T11 |
| Adjustable weights (skills, education, certifications, experience) | Missing: per-criterion weight only | Done | T18 |
| Names, street addresses, ZIP codes removed | Partial: see D-01, D-02 | Hardened; unmasked names never sent | T16 |
| Ranked shortlist with a score breakdown | Ranked, per-criterion only | Per-category breakdown for every candidate | T18 |
| Drafting anchored to core facts | Missing | Done | T20, T21 |
| A/B testing of versions against a role | Missing | Save and compare only; scoring versions is deferred | T22 |
| Improvement suggestions | Missing | Deferred past Nov 18 (baseline 6.5) | none |

## Defects

| ID | Severity | Defect | Status | Regression test |
|----|----------|--------|--------|-----------------|
| D-01 | High | Name never masked unless the first line was a clean 2-5 word name (same-line phone or email, pronouns, nickname, "Last, First", title line first) | Fixed | `test_masking_hardening.py::test_the_name_is_masked_wherever_it_sits_in_the_header` |
| D-02 | High | Street addresses and ZIP codes survived: ALL-CAPS or lower-case streets, full state names, "City ST, ZIP", bare ZIP, units, "Post Office Box" | Fixed | `test_masking_hardening.py::test_street_addresses_and_zip_codes_are_masked` |
| D-03 | High | With D-01 or D-02, raw PII reached the prompt, the audit log, and stored evidence; nothing checked the masked text | Fixed: a resume with no masked name is not sent (FR-42) | `test_defect_fixes.py::test_resume_without_a_masked_name_is_not_sent_to_the_model`, `test_end_to_end.py::test_no_name_street_address_or_zip_reaches_any_scoring_prompt` |
| D-04 | High | Later name forms missed: "McDonald" after "MCDONALD", hyphenated parts, curly apostrophes, the name inside handles and web addresses | Fixed | `test_masking_hardening.py` (name cases) |
| D-05 | Medium | A job title on the first line was treated as the name, masking the title everywhere and leaking the real name | Fixed | same |
| D-06 | Medium | Address and phone rules masked job text ("15 Court filings", "EN 50128", "C++11 14 17") and swallowed the employer above a city line | Fixed | `test_masking_hardening.py::test_job_relevant_text_is_left_alone`, `::test_the_employer_above_a_city_line_is_not_swallowed` |
| D-07 | Low | Empty or non-PDF uploads became permanent applicants and appeared in every run | Fixed | `test_defect_fixes.py::test_bad_uploads_are_rejected_without_creating_an_applicant` |
| D-08 | Low | The consistency check re-scored with today's penalty and model, reporting false differences | Fixed | `test_defect_fixes.py::test_consistency_check_uses_the_runs_own_penalty_and_model` |
| D-09 | Medium | Job-link import raised a traceback on malformed hosts, ports, and odd JSON-LD | Fixed | `test_defect_fixes.py` (job link cases) |
| D-10 | Medium | The folder browser crashed on `~nosuchuser`, scanned the working folder on an empty path, and let any visitor browse the server's disk | Fixed: no crash; `ALLOW_FOLDER_IMPORT` and `IMPORT_ROOT` for shared installs | `test_defect_fixes.py::test_folder_import_can_be_limited_or_turned_off` |
| D-11 | Medium | URL and handle rules masked skills and employers ("socket.io", "@Google") | Fixed for tech names and for handles outside the header | `test_masking_hardening.py::test_job_relevant_text_is_left_alone` |
| D-12 | Medium | A short name that is also an acronym or a month erased that word ("Ai" and "AI", "May 2021") | Fixed | `test_masking_hardening.py::test_short_names_do_not_erase_acronyms_or_months` |
| D-13 | Low | Folder import counted two identical files as two new resumes | Fixed | none (two-line change in `pages/2_Applicants.py`) |
| D-14 | Medium | Job-link import: DNS rebinding can bypass the private-address check (the host is resolved twice) | **Deferred.** NAT64 addresses are now refused; pinning the checked address needs a custom transport. Not exploitable on the demo VM without a hostile DNS server; fix before any public launch. | none |
| D-15 | Low | `MASK_GRAD_YEARS=true` also masks employment years on lines with a degree word | **Deferred.** Off by default. | none |
| D-16 | Low | Single-word names ("Madonna"), names split over two lines, and letter-spaced names are not found | **Deferred, mitigated:** such resumes are held for human review, not sent (FR-42). | `test_masking_hardening.py::test_a_resume_with_no_findable_name_is_reported_not_guessed` |
| D-17 | Low | A common word that is also the applicant's name is masked where capitalized ("Do code reviews" for "Lan Do") | **Accepted.** Masking errs toward hiding. | none |

## Known limits that are not defects

- City and state without a ZIP ("Chicago, IL") are not masked. The SOW requires street addresses and ZIP codes.
- The manager sees the original filename on Results and Audit. It never reaches a prompt or an audit payload.
- Resumes ingested before this build keep their old masked text. Re-import them to apply the new rules.
