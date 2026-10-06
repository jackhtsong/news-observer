-- Retire the natural-hazard layers (earthquake, wildfire, cyclone, flood, volcano, drought)
-- and the satellite population-exposure scores. Safe to re-run.
-- Apply with: python -m jobs.ingest.apply_schema
--
-- This deletes data. Run it on a database that should no longer hold hazards; do not run
-- it on a shared database that other branches still read hazards from.

DELETE FROM mart.event_link l
USING mart.event e
WHERE e.category IN ('earthquake', 'wildfire', 'cyclone', 'flood', 'volcano', 'drought')
  AND e.event_id IN (l.source_id, l.target_id);

-- Detail rows, tags, and exposure scores go with their events (ON DELETE CASCADE).
DELETE FROM mart.event
WHERE category IN ('earthquake', 'wildfire', 'cyclone', 'flood', 'volcano', 'drought');

DELETE FROM mart.event_kind
WHERE category IN ('earthquake', 'wildfire', 'cyclone', 'flood', 'volcano', 'drought');

DROP VIEW IF EXISTS mart.v_earthquake;
DROP VIEW IF EXISTS mart.v_wildfire;
DROP VIEW IF EXISTS mart.v_cyclone;
DROP VIEW IF EXISTS mart.v_flood;
DROP VIEW IF EXISTS mart.v_volcano;
DROP VIEW IF EXISTS mart.v_drought;

DROP TABLE IF EXISTS mart.hazard_exposure;
DROP TABLE IF EXISTS mart.wildfire_hotspot;
DROP TABLE IF EXISTS mart.earthquake;
DROP TABLE IF EXISTS mart.wildfire;
DROP TABLE IF EXISTS mart.cyclone;
DROP TABLE IF EXISTS mart.flood;
DROP TABLE IF EXISTS mart.volcano;
DROP TABLE IF EXISTS mart.drought;
