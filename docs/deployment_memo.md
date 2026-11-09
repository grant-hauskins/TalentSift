# Deployment memo: demo VM, LLM cap, packaging (T06, WBS 2.4)

**Status: draft for sign-off at T07.** The recommendations below are proposals. Provider choice and the spend
cap are sponsor-level decisions for all three founders (Planning Report section 6.1).

**Bottom line:** run the team's demo on one free-tier Linux VM with a persistent disk, behind a password, with a
$2.00 per-batch LLM cap and a hard $50 limit on the OpenRouter key. Ship the product as a tagged GitHub release
plus an install guide. Keep Streamlit Community Cloud as the fallback.

## 1. Demo VM

| Option | Cost | Persistent disk | Fit |
|--------|------|-----------------|-----|
| **Free-tier VM (recommended): Google Cloud e2-micro or Oracle Cloud Always Free** | $0 | Yes | Matches what a customer does: own VM, own key. Needs about 30 minutes of setup |
| Streamlit Community Cloud (fallback) | $0 | No: disk resets on reboot | Already works (`AUTO_SEED_DEMO`). No backups, candidate profiles are lost on reboot |
| Founder laptop (last resort) | $0 | Yes | Works offline. Not reachable by pilot users |

- **Recommendation:** a free-tier VM. Damian picks the provider at sign-off after confirming the current
  free-tier terms, which could not be checked from the build environment.
- **Sizing:** 1 vCPU and 1 GB RAM with 1 GB swap is enough for a few concurrent users and batches of up to 100
  resumes, because scoring time is spent waiting on the LLM, not on the VM.
- **Access:** the MVP has no login. The demo sits behind a reverse proxy with one shared password, or an SSH
  tunnel (install guide, step 6). Folder import is turned off on the VM.

## 2. LLM spend

| Control | Value | Where |
|---------|-------|-------|
| Sprint cap | $50 | Hard limit set on the OpenRouter key itself |
| Per-batch cap | $2.00 | `COST_CAP_USD`. A run stops cleanly when reached; unscored applicants go to Needs review |
| Weekly budget | $10 / $15 / $25 | Planning Report Table 5. Grant tracks actuals in the workbook |
| Free re-runs | Cached scores | Same resume, rubric, prompt, and model = no new call |
| Zero-spend path | Offline client | All tests, CI, rehearsals, and the outage fallback |

Estimate, to be replaced with the real number after the first live batch: at a few cents per resume (SOW
assumption), a 26-resume batch costs under $1. The $2.00 cap leaves headroom for retries.

Drafting on the candidate side costs one call per draft (two on a retry). It has no separate cap in the MVP;
spend is bounded by the limit on the key.

## 3. Packaging and licensing

- **What ships:** a tagged release of this repository, `docs/install.md`, `.env.example`,
  `deploy/talentsift.service`, and `scripts/backup.py`.
- **What the customer supplies:** a Linux VM (or cloud account), Python 3.11, and their own OpenRouter key.
- **Tenancy:** one employer organization per install. No per-role permissions in the MVP.
- **Data:** SQLite and resume files on the host's disk, outside the code folder (`TALENTSIFT_DATA_DIR`), with a
  scheduled backup.
- **License model (SOW section 4):** annual self-hosted license in three tiers. No license enforcement is built
  into the MVP.

## 4. Open items for sign-off

1. Choose the VM provider and who holds the account.
2. Confirm the per-batch cap ($2.00) and set the $50 limit on the key.
3. Decide how pilot users reach the demo: shared password, or founders drive the session.
4. Confirm the model ids in `talentsift/model_catalog.py` against openrouter.ai/models.
