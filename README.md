# linkedin-hunter

Job discovery, ATS tailoring and outreach pipeline. It watches public job
sources, sends you a review card for each new role, and only spends AI tokens
on the roles you explicitly approve.

```
n8n (08:00 / 18:00)  →  bridge worker (:8766)  →  Telegram bot
  ├─ SerpAPI (Google)     ├─ dedup vs PostgreSQL    ├─ /hunt on demand
  ├─ LinkedIn guest       ├─ review cards           ├─ Approve / Ignore
  └─ dedupe li-<id>       └─ AI + Typst PDF         └─ PDF + recruiter note
```

## How it works

1. **Discovery is free.** The scheduled workflow and `/hunt` scan public
   listings, dedupe by LinkedIn job id, and store new roles as `PENDING_REVIEW`.
2. **You decide.** Each new role arrives as a Telegram card with the job
   description and employer details, plus **Generate Application** / **Ignore**.
3. **Only approved roles cost tokens.** Approving tailors your CV against the
   job description, compiles an ATS-friendly PDF with Typst, and drafts a
   recruiter note. Ignoring marks the role `DISCARDED` so it never resurfaces.

## Setup

```bash
cp .env.example .env        # fill in the four values
chmod 600 .env
cp profile/master_profile.example.md profile/master_profile.md   # your CV
./scripts/hunter.sh start   # bridge on :8766
python3 bridge/telegram_bot.py   # bot daemon
```

Requires Python 3.11+, Typst, PostgreSQL, and a Docker `postgres` container
named `octopus_postgres` (see `scripts/hunter.sh` and `sql/`).

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /ping` | liveness + provider status |
| `GET /stats` | counters |
| `GET /seen?external_id=` | dedup check |
| `POST /ingest` | store jobs, send review cards |
| `POST /approve` | tailor an approved job |
| `POST /ignore` | discard a job |
| `POST /tailor` | immediate tailoring (pasted link) |

All but `/ping` require `Authorization: Bearer $BRIDGE_AUTH_TOKEN`.

## Disclaimer / zero-ban policy

No LinkedIn login, no session cookies, no browser automation, no automated
"Easy Apply". Discovery uses public search results and public guest pages only.
Every application is submitted manually by you.

## License

MIT
