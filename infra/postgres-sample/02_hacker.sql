-- Bug-bounty "hacker" datasource: one schema per program owner (company). Read-only via MCP.
CREATE DATABASE hacker;
\connect hacker

CREATE SCHEMA acme;
CREATE SCHEMA globex;

CREATE TABLE acme.reports (id serial PRIMARY KEY, title text NOT NULL, severity text NOT NULL, hacker_handle text NOT NULL, bounty_usd numeric(10,2), status text NOT NULL DEFAULT 'triaged', submitted_at timestamptz DEFAULT now());
CREATE TABLE globex.reports (LIKE acme.reports INCLUDING ALL);

INSERT INTO acme.reports (title, severity, hacker_handle, bounty_usd, status) VALUES
  ('IDOR on /api/invoices', 'high', 'h4x0r', 1500, 'resolved'),
  ('Reflected XSS in search', 'medium', 'nullbyte', 300, 'triaged');
INSERT INTO globex.reports (title, severity, hacker_handle, bounty_usd, status) VALUES
  ('SSRF via webhook URL', 'critical', 'pwnie', 5000, 'resolved');

CREATE TABLE acme.hackers (handle text PRIMARY KEY, reputation int NOT NULL DEFAULT 0);
INSERT INTO acme.hackers VALUES ('h4x0r', 120), ('nullbyte', 40);
CREATE TABLE globex.hackers (LIKE acme.hackers INCLUDING ALL);
INSERT INTO globex.hackers VALUES ('pwnie', 900);
