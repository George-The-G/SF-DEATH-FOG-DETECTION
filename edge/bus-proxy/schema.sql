-- D1 schema for the bus GPS log. Create once:  npx wrangler d1 create bus-log   then   npx wrangler d1 execute bus-log --remote --file=schema.sql
-- One row per bus report from 511. (id, t) is the key, so reading the same report twice stores it once. t is the epoch second the bus RECORDED the position.
CREATE TABLE IF NOT EXISTS fixes (
  id   TEXT    NOT NULL,   -- vehicle id
  line TEXT    NOT NULL,   -- Muni line number ("19", "66")
  t    INTEGER NOT NULL,   -- epoch seconds the position was recorded
  lat  REAL    NOT NULL,   -- 5 decimals, about 1 m
  lon  REAL    NOT NULL,
  brg  INTEGER,            -- compass bearing, degrees
  PRIMARY KEY (id, t)
);
CREATE INDEX IF NOT EXISTS fixes_line_t ON fixes (line, t);
