-- Postgres bootstrap for lumid.data: database, lumid_data_meta schema,
-- the admin role used by /sql/v1's SET LOCAL ROLE, and the timescaledb
-- extension (used opt-in per stream for hypertables).
--
-- This file is mounted into the postgres image's docker-entrypoint-initdb.d/
-- and runs once on first startup of the volume.

CREATE DATABASE lumid_data;

\connect lumid_data

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- /sql/v1 issues ``SET LOCAL ROLE <DB_ADMIN_ROLE>`` for every call so any
-- schema-level GRANTs apply consistently.
CREATE ROLE app_admin LOGIN PASSWORD 'app_admin';

-- The app's lifespan startup runs idempotent DDL as app_admin.
GRANT CREATE ON DATABASE lumid_data TO app_admin;

-- Schema layout: the public schema holds user data; lumid_data_meta is
-- service-internal (audit_log, agent_runs).
CREATE SCHEMA IF NOT EXISTS lumid_data_meta AUTHORIZATION app_admin;
GRANT USAGE, CREATE ON SCHEMA public TO app_admin;
GRANT USAGE ON SCHEMA lumid_data_meta TO app_admin;
