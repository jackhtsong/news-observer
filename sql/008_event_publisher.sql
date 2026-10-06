-- Local news events: who published them, in what language, and the enrichment verdict.
-- Safe to re-run (idempotent).
--
-- Apply with:
--   psql "$DATABASE_URL" -f sql/008_event_publisher.sql
--
-- Written by jobs/news/enrich.py when it promotes raw.feed_item rows into mart.event.
-- On these rows country_iso3 and lat/lng are where the story happens.

ALTER TABLE mart.event ADD COLUMN IF NOT EXISTS publisher_id text REFERENCES mart.publisher (publisher_id);
ALTER TABLE mart.event ADD COLUMN IF NOT EXISTS language text;
ALTER TABLE mart.event ADD COLUMN IF NOT EXISTS original_title text;  -- before translation to English
ALTER TABLE mart.event ADD COLUMN IF NOT EXISTS verification jsonb;   -- {aboutCountryIso3, isLocal, model, at}
CREATE INDEX IF NOT EXISTS event_country_recent ON mart.event (country_iso3, occurred_at DESC);
