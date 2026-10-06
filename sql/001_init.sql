-- Eye of Horus core schema (plain Postgres; no extensions needed, so Neon's free plan works).
-- Safe to re-run. Apply every migration in order with: python -m jobs.ingest.apply_schema
--
-- The natural-hazard detail tables, their views, and the TimescaleDB hotspot hypertable that
-- earlier versions created here were retired; sql/009_drop_hazards.sql removes them from an
-- older database.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS mart;

CREATE TABLE IF NOT EXISTS raw.ingest_batch (
  batch_id text PRIMARY KEY,
  source text NOT NULL,
  pulled_at timestamptz NOT NULL,
  payload jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS mart.event (
  event_id text PRIMARY KEY,
  source text NOT NULL,
  source_event_id text NOT NULL,
  category text NOT NULL,
  subtype text,
  title text NOT NULL,
  summary text,
  info_url text,
  occurred_at timestamptz NOT NULL,
  updated_at timestamptz,
  ended_at timestamptz,
  lng double precision NOT NULL,
  lat double precision NOT NULL,
  alt_m double precision,
  geo_precision text NOT NULL,
  geo_source text NOT NULL,
  significance double precision NOT NULL,
  weight double precision,
  country_iso3 text,
  entities jsonb NOT NULL DEFAULT '[]'::jsonb,
  keywords jsonb NOT NULL DEFAULT '[]'::jsonb,
  raw_ref text,
  ingested_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source, source_event_id)
);

CREATE INDEX IF NOT EXISTS event_occurred_category
  ON mart.event (occurred_at DESC, category);

CREATE TABLE IF NOT EXISTS mart.event_tag (
  event_id text NOT NULL REFERENCES mart.event (event_id) ON DELETE CASCADE,
  tag text NOT NULL,
  tag_kind text NOT NULL,
  PRIMARY KEY (event_id, tag)
);

CREATE INDEX IF NOT EXISTS event_tag_tag ON mart.event_tag (tag);

CREATE TABLE IF NOT EXISTS mart.event_kind (
  category text PRIMARY KEY,
  detail_table text NOT NULL,
  label text NOT NULL
);

CREATE TABLE IF NOT EXISTS mart.event_link (
  link_id text PRIMARY KEY,
  source_id text NOT NULL,
  target_id text NOT NULL,
  relation text NOT NULL,
  confidence double precision NOT NULL,
  rationale text,
  citations jsonb NOT NULL DEFAULT '[]'::jsonb,
  model text
);
