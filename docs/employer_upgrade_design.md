# Employer upgrade design: category weights and model list (T11, WBS 3.4)

**Bottom line:** the hiring manager sets four weights on the Roles page, sees the split before approving, and
gets a per-category score breakdown for every candidate on Results. The AI model is chosen from a short list on
Settings, with Claude as the default.

## Category weights

**Roles page.** Four number inputs (0-100, step 5) above the criteria table: Skills, Education, Certifications,
Experience. A caption shows the current split as percentages of the categories the rubric uses. The criteria
table has a Category column next to Type and Weight.

**Rules**
- Only relative size matters: 50/50 and 10/10 are the same split.
- A category with no criteria is ignored, whatever its weight.
- At least one category that has criteria must have a weight above 0.
- Saving a weight change on an approved rubric creates a new draft version. The manager approves it before
  screening, as with any rubric change.

**Score**

    category score = weighted average of the category's 0-4 criterion scores, as 0-100
                     (criterion weights: high = 3, medium = 2, low = 1)
    category share = category weight / total weight of the categories in use
    fit score      = sum(category share x category score) - 15 x (must-haves scored 0 or 1), never below 0

**Worked example** (weights: skills 60, experience 20, education 15, certifications 5; all four in use)

| Category | Category score | Share | Points |
|----------|----------------|-------|--------|
| Skills | 95.6 | 60% | 57.4 |
| Education | 75.0 | 15% | 11.2 |
| Certifications | 25.0 | 5% | 1.2 |
| Experience | 75.0 | 20% | 15.0 |
| **Fit score** | | | **84.9** (no must-have penalty) |

**Results page.** Shortlist cards carry a one-line breakdown ("Skills 96 x 60% · ..."). Every candidate's detail
panel has the table above, plus the penalty line when one applies. The run header shows the weights the run used.

**Screen page.** Shows the split for the chosen role before the run starts.

## Model list

| Model | OpenRouter id | Use |
|-------|---------------|-----|
| Claude Sonnet 4.5 (default) | `anthropic/claude-sonnet-4.5` | Best balance of accuracy and cost |
| Claude Haiku 4.5 | `anthropic/claude-haiku-4.5` | Fastest and cheapest Claude; large batches |
| GPT-4o mini | `openai/gpt-4o-mini` | Low-cost alternative during an Anthropic outage |
| Gemini 2.5 Flash | `google/gemini-2.5-flash` | Low-cost alternative |

- Chosen on the Settings page; "Other" accepts any OpenRouter model id.
- Every run records the model it used. Cached scores are kept per model, so switching back costs nothing.
- **Action before release:** confirm these ids at openrouter.ai/models. They were not reachable from the build
  environment, so they are unverified. The list lives in one file, `talentsift/model_catalog.py`.
- **Deferred:** a per-run model picker on the Screen page (baseline task 5.2).

## Open questions for the design review (T12)

1. Default weights for a new role are equal (25 each). Should the default favor skills and experience?
2. Should a category weight of 0 hide that category's criteria from the model as well? Today they are still
   scored and shown, with 0% share.
