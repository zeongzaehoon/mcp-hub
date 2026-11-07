CREATE DATABASE IF NOT EXISTS weird_db;
CREATE TABLE IF NOT EXISTS weird_db.`Mixed Case`
(
    company_id String,
    id         UUID,
    ip         IPv4,
    m          Map(String, UInt64),
    t          Tuple(String, UInt8),
    arr        Array(Nullable(String)),
    dec        Decimal(18, 4),
    ts         DateTime64(3, 'UTC'),
    e          Enum8('x' = 1, 'y' = 2),
    f          Nullable(Float64),
    d          Date,
    `select`   String
) ENGINE = MergeTree ORDER BY (company_id, id);
INSERT INTO weird_db.`Mixed Case` VALUES
  ('acme',   generateUUIDv4(), '10.0.0.1', map('a', 1, 'b', 2), ('t', 1), ['x', NULL], 1234.5678, '2026-09-01 10:00:00.123', 'x', 1.5, '2026-09-01', 'kw'),
  ('acme',   generateUUIDv4(), '10.0.0.2', map(),               ('u', 2), [],          -0.0001,   '2026-09-02 10:00:00.000', 'y', NULL, '2026-09-02', 'kw2'),
  ('globex', generateUUIDv4(), '10.0.0.3', map('z', 9),         ('v', 3), ['g'],       99,        '2026-09-03 10:00:00.000', 'x', NULL, '2026-09-03', 'kw3');
CREATE TABLE IF NOT EXISTS weird_db.`Empty` (company_id String, v UInt8) ENGINE = MergeTree ORDER BY company_id;
