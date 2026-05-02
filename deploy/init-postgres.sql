-- Postgres init for shared multi-service deployment.
-- Creates separate databases for lumid.data, lumilake, and flowmesh
-- so the three services can share one Postgres instance.

CREATE DATABASE lumid_data;
CREATE DATABASE lumilake;
CREATE DATABASE flowmesh;

CREATE USER lumid_data WITH PASSWORD 'lumid_data';
CREATE USER lumilake WITH PASSWORD 'lumilake';
CREATE USER flowmesh WITH PASSWORD 'flowmesh';

GRANT ALL PRIVILEGES ON DATABASE lumid_data TO lumid_data;
GRANT ALL PRIVILEGES ON DATABASE lumilake TO lumilake;
GRANT ALL PRIVILEGES ON DATABASE flowmesh TO flowmesh;
