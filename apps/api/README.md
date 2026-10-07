# Eye of Horus API

A read-only FastAPI service. With `DATABASE_URL` set, it serves events and links from Postgres
(`mart.event`, `mart.event_link`), leaving out any row that doesn't match the shared schema; if
the database can't be reached it serves the last-good `data/snapshots` copy, or a `503`. It
never serves the sample `data/fixtures` while a database is configured; those are only for
running without one. It never writes to the database; the daily jobs in
[`jobs/`](../../jobs/README.md) do that.

## Run locally

Python 3.12+. Copy `.env.example` to `.env` at the repository root. Leave `DATABASE_URL`
empty to serve the sample data.

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 43124
```

```bash
curl http://127.0.0.1:43124/health
curl "http://127.0.0.1:43124/events?limit=5"
curl http://127.0.0.1:43124/links
```

Run the tests with `python -m unittest discover -s tests -v` from `apps/api`. They use temporary
directories and simulated database failures, never a real database or credentials.

## Routes

| Route | Returns |
| --- | --- |
| `GET /health` | `ok`, `database` (`ok`, `fixture`, `error`, or `dark`), `dataSource`, `usingFixtures`, `sourceStatus`, `warmupMs`, `lastIngestAt` |
| `GET /events` | `{generatedAt, sourceStatus, events, nextCursor}` |
| `GET /events/{id}` | One Event |
| `GET /events/{id}/links` | The EventLinks that touch that event |
| `GET /links` | Every EventLink |

`/events` query parameters: `types` (comma-separated `layerId`s), `start` (inclusive) and `end`
(exclusive) as timezone-aware ISO timestamps, `minSignificance`, `limit` (default 2000, max 8000),
and `cursor` (from `nextCursor`). Events and links follow the
shared schemas in [`packages/schema/`](../../packages/schema). Only categories that
`event.schema.json` lists are read from the database.

Rows that fail the schema are logged and omitted, even if that leaves none; `/health` reports how
many in `detail`. A database outage serves the snapshot, or a `503` when there is none. A usable
snapshot can report `ok: true` while `database` reports the outage.
`sourceStatus` keys are `news`, `gdelt`, and `database`; feed status is `unknown` unless a job
recorded it, and `fixture` for sample data.

Database connections require TLS, time out after five seconds, and limit statements to ten
seconds. Reads load the complete dataset before filtering and paging, which suits the
90-day window the daily cleanup keeps.

## Environment

Process variables take precedence, then the repository-root `.env`, then a legacy `.env.local`.

| Variable | Notes |
| --- | --- |
| `DATABASE_URL` | Neon Postgres connection string. Empty means sample data. |
| `CORS_ORIGIN` | Optional, comma-separated. The website calls the API from its server, so browsers never need CORS. |

## Deploy on Vercel

The API is its own Vercel project built from the **repository root**: [`server.py`](../../server.py)
loads this app, [`pyproject.toml`](../../pyproject.toml) lists its dependencies, and
[`vercel.json`](../../vercel.json) keeps the website and jobs out of the function bundle. Set
`DATABASE_URL` in the project's environment variables. The disk is read-only there, so no
snapshot is written; an outage returns a `503`. Keep the versions in
`pyproject.toml` in step with `requirements.txt`.
