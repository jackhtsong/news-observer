# Last-good API snapshots

When the API runs on a machine with a writable disk, it saves validated database events to
`events.json` and links to `links.json`. Request filters and page size do not affect either
snapshot. Runtime JSON files are ignored by Git. On Vercel the disk is read-only, so no
snapshot is written and an outage returns an error.

`meta.json` contains `lastIngestAt`, `savedAt`, and `eventCount`. A job may also write a
`sourceStatus` object for `news` and `gdelt`; the API preserves it. A successful database
read does not imply that the daily feeds ran.

Every file is replaced atomically. The files are not a transaction, so metadata can lag if a
write fails.

On database failure, the API serves the snapshot, never `data/fixtures/`; those samples are
used only when no database is configured. Rows that fail the shared schemas in
`packages/schema/` are left out.
