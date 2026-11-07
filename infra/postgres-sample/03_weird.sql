-- "Weird" fixture: the server must cope with identifiers and types it has never seen.
\connect sample
CREATE SCHEMA "Weird-Tenant";
CREATE TYPE "Weird-Tenant".mood AS ENUM ('happy', 'meh');
CREATE TABLE "Weird-Tenant"."Mixed Case" (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  amount numeric(20,6) NOT NULL,
  big bigint,
  tags text[] NOT NULL DEFAULT '{}',
  meta jsonb,
  span int4range,
  at timestamptz NOT NULL DEFAULT now(),
  raw bytea,
  dur interval,
  ip inet,
  feeling "Weird-Tenant".mood,
  "select" text,           -- reserved word as a column name
  "Spaced Col" text,
  nothing text
);
INSERT INTO "Weird-Tenant"."Mixed Case" (amount, big, tags, meta, span, raw, dur, ip, feeling, "select", "Spaced Col")
VALUES (12345.678901, 9007199254740993, ARRAY['a','b'], '{"k": [1, {"n": null}]}', int4range(1, 10), '\xDEADBEEF', interval '1 day 2 hours', '10.1.2.3', 'happy', 'kw', 'sp'),
       (0.5, NULL, '{}', NULL, 'empty', NULL, NULL, NULL, NULL, NULL, NULL);
CREATE VIEW "Weird-Tenant"."V iew" AS SELECT id, amount FROM "Weird-Tenant"."Mixed Case";
CREATE TABLE "Weird-Tenant"."Empty" (id int);
