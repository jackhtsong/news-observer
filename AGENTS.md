# Repository Guidelines

## Project structure and module organization

`README.md` describes the product, data flow, setup, and deployment. Read
`apps/api/README.md` before changing API contracts and `jobs/README.md` before
changing the data jobs. `docs/architecture.md` and `docs/business-model.md` are
the original hackathon briefs, kept as history.

- `apps/api/` contains the read-only FastAPI app and its tests. `server.py`,
  `pyproject.toml`, and `vercel.json` at the root deploy it on Vercel.
- `apps/web/` contains the Next.js globe and same-origin API proxy.
- `jobs/` contains the daily data jobs: RSS news (`jobs/ingest/rss.py`,
  `jobs/news/`), GDELT conflict events (`jobs/ingest/gdelt.py`, `run.py`),
  Gemini story linking (`jobs/llm/`), and cleanup (`jobs/ingest/prune.py`).
- `sql/` holds the Postgres migrations; `jobs/ingest/apply_schema.py` applies them.
- `packages/schema/` holds the shared Event and EventLink JSON schemas.
- `data/fixtures/` contains bundled sample events and links. `data/snapshots/`
  holds generated fallback data; runtime JSON files are ignored by Git.
- `.github/workflows/` runs the tests on every push and the jobs once a day.

## Build, test, and development commands

Use Python 3.12+. From `apps/api`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 43124
python -m unittest discover -s tests -v
```

These commands create the environment, install dependencies, run the API, and
execute tests. Leave `DATABASE_URL` unset for fixtures. Read
`apps/api/README.md` for environment variables and current integration limits.
From `apps/web`, use `npm ci`, `npm test`, `npm run lint`, and `npm run build`.
Run `git diff --check` before submitting changes.

## Coding style and naming conventions

Match existing Python code: four-space indentation, snake_case helpers, and
type hints on application functions. Preserve JSON contract names such as
`sourceUrl`, `occurredAt`, and `geoPrecision`. Use two spaces in JSON examples.
No formatter or linter is configured. Keep Markdown links relative within docs.

## Testing guidelines

Use standard-library `unittest` and FastAPI `TestClient`. Name files `test_*.py`
under `apps/api/tests/` or the job's `tests/` folder. Tests must isolate caches in
temporary directories, simulate database failures without credentials, and make
no network calls. Cover filters, schema compatibility, and fallback behavior when
changing those paths. Run job tests from the root, for example
`python -m unittest discover -s jobs/ingest/tests -t .`.
There is no coverage threshold. Distinguish simulated checks from live-service
validation in PRs.

## Commit and pull request guidelines

Existing commits use imperative subjects, such as `Add business and architecture
briefs`. Keep PRs focused, link their issue, describe behavior changes and
validation, and include screenshots for UI changes. Call out shared-contract
changes explicitly.

## Architecture and configuration

Keep FastAPI as the browser-facing API; access Postgres (Neon) and Gemini
server-side only. Never serve sample fixtures while a database is configured: omit rows that
fail the schema and show the rest. Keep credentials out of commits, and
retain source attribution: list every data source in the README credits and in
`apps/web/src/components/data-attribution.tsx`. Report unknown feed health
honestly. Keep the daily jobs within free tiers (Gemini call budgets, Neon's
0.5 GB via the 90-day cleanup).
