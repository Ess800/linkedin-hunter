# AGENTS.md — linkedin-hunter

## Workspace Identity
- **Project:** LinkedIn Job Hunting, ATS Tailoring & InMail Outreach Pipeline
- **Path:** `/home/user/projects/linkedin-hunter`
- **Stack:** Python 3.14, Typst 0.15.1, PostgreSQL 16, n8n, DeepSeek (`deepseek-chat`)

## Architecture
```
n8n (Docker :5678)            →  Bridge Worker (:8766)        →  Telegram Bot
  ├─ Twice daily 08:00/18:00      ├─ Dedup vs PostgreSQL          ├─ /hunt on demand
  ├─ SerpAPI (Google) search      ├─ Review cards (no AI spend)   ├─ Approve / Ignore buttons
  ├─ LinkedIn guest listings      ├─ DeepSeek tailoring           ├─ PDF + brief + recruiter note
  └─ Dedupe by li-<id>            └─ Typst PDF + storage          └─ Match score + apply link
```

**Discovery is free.** New jobs land as `PENDING_REVIEW` and are sent as one card each with
a role snapshot, the main responsibilities and key requirements (extracted locally, no AI).
**Generate Application** calls `/approve` (DeepSeek → Typst PDF); **Ignore** sets `DISCARDED`
and the job is never offered again. **Regenerate** re-tailors against the newest uploaded
profile without asking twice. Postings reserved for UAE nationals or requiring a language
other than English/Arabic are dropped at ingest (`bridge/job_filters.py`).

**AI fallback chain:** DeepSeek key pool (round-robin on 429/402, 90 s cooldown) →
deterministic heuristic engine. There is no local model and no Gemini dependency.

## Bridge endpoints (all POST/GET except `/ping` require `Authorization: Bearer …`)
| Endpoint | Purpose |
|---|---|
| `GET /ping` | Liveness + AI/provider status |
| `GET /stats` | Job / tailored / score counters |
| `GET /seen?external_id=` | Dedup check used by the bot and workflow |
| `POST /ingest` | Store discovered jobs as `PENDING_REVIEW`, send review cards |
| `POST /approve` | Tailor an approved job, return PDF path + brief |
| `POST /regenerate` | Re-tailor with the newest profile (approved or discarded job) |
| `POST /ignore` | Mark a job `DISCARDED` |
| `POST /tailor` | Immediate tailoring (pasted link / manual text) |
| `POST /reload-profile` | Hot-reload `profile/master_profile.md` |

## Key Files
| File | Purpose |
|------|---------|
| `profile/master_profile.md` | Canonical candidate dossier (single source of truth) |
| `templates/resume.typ` | ATS-optimized Typst resume template |
| `bridge/job_filters.py` | Nationality/language exclusions + free role extraction |
| `bridge/job_worker.py` | HTTP bridge service (port 8766), AI + PDF + DB |
| `bridge/telegram_api.py` | Shared Telegram transport + review-card formatting |
| `bridge/telegram_bot.py` | Bot daemon: polling, /hunt, approve/ignore callbacks, resume wizard |
| `n8n/linkedin_hunter_workflow.json` | Automated discovery workflow (08:00 / 18:00 Asia/Dubai) |
| `sql/001_job_hunter_schema.sql` | PostgreSQL schema migration |
| `scripts/hunter.sh` | Service management CLI |
| `output/` | Compiled PDFs, Typst sources and logs (not tracked) |

## Zero-Ban Policy
- NO LinkedIn login or session cookies
- NO Selenium/Playwright automation against LinkedIn
- NO automated "Easy Apply" clicks
- Job discovery via PUBLIC sources only: SerpAPI (Google) results + LinkedIn public guest pages
- All final applications are MANUAL via Telegram links
