#!/bin/sh
# Prepare the database, then hand over to whatever CMD was given.
set -e

echo "MediQueue: preparing database at ${DATABASE_URL}"

# `seed` creates the tables if they are missing and loads the demo data only
# when the database is empty, so it is safe to run on every start.
flask --app wsgi seed

echo "MediQueue: starting $*"
exec "$@"
