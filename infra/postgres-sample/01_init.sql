-- Two tenant schemas so isolation can be verified end-to-end.
CREATE SCHEMA acme;
CREATE SCHEMA globex;

CREATE TABLE acme.customers (id serial PRIMARY KEY, name text NOT NULL, email text NOT NULL, created_at timestamptz DEFAULT now());
CREATE TABLE globex.customers (id serial PRIMARY KEY, name text NOT NULL, email text NOT NULL, created_at timestamptz DEFAULT now());

INSERT INTO acme.customers (name, email) VALUES ('Acme Alice', 'alice@acme.example'), ('Acme Bob', 'bob@acme.example');
INSERT INTO globex.customers (name, email) VALUES ('Globex Gina', 'gina@globex.example'), ('Globex Greg', 'greg@globex.example');

-- shared table for column-mode tenants (used by clickhouse later; kept here for parity)
CREATE TABLE public.events (id serial PRIMARY KEY, company_id text NOT NULL, kind text NOT NULL, payload jsonb, ts timestamptz DEFAULT now());
INSERT INTO public.events (company_id, kind, payload) VALUES ('acme', 'login', '{}'), ('globex', 'login', '{}');
