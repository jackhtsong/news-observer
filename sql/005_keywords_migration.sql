-- Rename entities column to keywords in mart.event
-- Safe to re-run (idempotent)
--
-- Apply with:
--   psql "$DATABASE_URL" -f sql/005_keywords_migration.sql
--
-- Context:
--   - News events will have populated keywords arrays
--   - Conflict events keep empty keywords; their actors are in entities

-- Older databases had only `entities`; it is renamed. When keywords already exists (a fresh
-- database from sql/001_init.sql, or a re-run), nothing changes: entities stays for conflict
-- actors (sql/003_acled.sql).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'mart'
          AND table_name = 'event'
          AND column_name = 'keywords'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'mart'
              AND table_name = 'event'
              AND column_name = 'entities'
        ) THEN
            ALTER TABLE mart.event RENAME COLUMN entities TO keywords;
            RAISE NOTICE 'Renamed entities → keywords';
        ELSE
            ALTER TABLE mart.event ADD COLUMN keywords jsonb NOT NULL DEFAULT '[]'::jsonb;
        END IF;
    END IF;

    -- Verify keywords column exists
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'mart'
          AND table_name = 'event'
          AND column_name = 'keywords'
    ) THEN
        RAISE EXCEPTION 'keywords column does not exist after migration';
    END IF;
END $$;

-- Update column comment
COMMENT ON COLUMN mart.event.keywords IS
  'Searchable keywords/tags extracted from event data. Populated for news events (topics, entities, concepts). Empty for conflict events (actors are in entities).';
