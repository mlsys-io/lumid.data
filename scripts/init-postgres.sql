-- Postgres bootstrap for lumid.data: roles, database, lumid_data_meta schema,
-- and the timescaledb extension (used opt-in per stream for hypertables).
--
-- This file is mounted into the postgres image's docker-entrypoint-initdb.d/
-- and runs once on first startup of the volume.

CREATE DATABASE lumid_data;

\connect lumid_data

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Roles. PostgREST uses a JWT-issued ``role`` claim to switch session role;
-- our PostgrestJwtConfig maps PrincipalContext scopes to one of these.
CREATE ROLE postgrest_anon NOLOGIN;
CREATE ROLE app_user NOLOGIN;
CREATE ROLE app_admin LOGIN PASSWORD 'app_admin';

GRANT postgrest_anon TO app_admin;
GRANT app_user TO app_admin;

-- The app's lifespan startup runs idempotent DDL as app_admin.
GRANT CREATE ON DATABASE lumid_data TO app_admin;

-- Schema layout: the public schema holds user data; lumid_data_meta is
-- service-internal (audit_log, agent_runs).
CREATE SCHEMA IF NOT EXISTS lumid_data_meta AUTHORIZATION app_admin;
GRANT USAGE ON SCHEMA public TO postgrest_anon, app_user;
GRANT USAGE, CREATE ON SCHEMA public TO app_admin;
GRANT USAGE ON SCHEMA lumid_data_meta TO app_admin;

-- Default grants so future tables created in public are visible to roles.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO postgrest_anon, app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT INSERT, UPDATE, DELETE ON TABLES TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_user;
