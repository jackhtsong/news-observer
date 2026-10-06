"""Vercel entrypoint for the FastAPI app in apps/api.

The API Vercel project builds from the repository root because the app reads files outside
apps/api: the shared schemas in packages/schema and the sample data in data/fixtures.
Locally, run apps/api/main.py with uvicorn instead (see apps/api/README.md).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "apps" / "api"))

from main import app  # noqa: E402,F401
