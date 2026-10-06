-- Which news stories the linker (jobs/llm/link_events.py) has already compared with others.
-- Safe to re-run (idempotent). NULL means not yet processed; each daily run only analyses
-- those, so Gemini calls scale with new stories rather than with the whole table.

ALTER TABLE mart.event ADD COLUMN IF NOT EXISTS linked_at timestamptz;
