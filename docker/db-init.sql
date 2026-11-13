CREATE EXTENSION IF NOT EXISTS vector;
-- Separate database for the integration test suite.
CREATE DATABASE scout_test OWNER scout;
\c scout_test
CREATE EXTENSION IF NOT EXISTS vector;
