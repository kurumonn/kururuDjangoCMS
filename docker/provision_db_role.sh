#!/bin/sh
# PostgreSQL管理者資格情報を使う唯一のアプリ側処理。
# 管理者、DDL所有者、Web用DML roleを分離し、既存volumeも同じ状態へ収束させる。
set -eu

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_MIGRATION_USER:?POSTGRES_MIGRATION_USER is required}"
: "${POSTGRES_MIGRATION_PASSWORD:?POSTGRES_MIGRATION_PASSWORD is required}"
: "${POSTGRES_APP_USER:?POSTGRES_APP_USER is required}"
: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD is required}"

if [ "$POSTGRES_MIGRATION_USER" = "$POSTGRES_USER" ] \
    || [ "$POSTGRES_APP_USER" = "$POSTGRES_USER" ] \
    || [ "$POSTGRES_MIGRATION_USER" = "$POSTGRES_APP_USER" ] \
    || [ "$POSTGRES_MIGRATION_USER" = "postgres" ] \
    || [ "$POSTGRES_APP_USER" = "postgres" ]; then
    echo "[db-role] administrator, migration owner and app role must differ" >&2
    exit 64
fi

export PGPASSWORD="$POSTGRES_PASSWORD"

psql -v ON_ERROR_STOP=1 --host=db --username="$POSTGRES_USER" --dbname=postgres <<'SQL'
\getenv app_db POSTGRES_DB
\getenv migration_user POSTGRES_MIGRATION_USER
\getenv migration_password POSTGRES_MIGRATION_PASSWORD
\getenv app_user POSTGRES_APP_USER
\getenv app_password POSTGRES_APP_PASSWORD

SELECT format(
    'CREATE ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'migration_user',
    :'migration_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migration_user')
\gexec
SELECT format(
    'ALTER ROLE %I WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'migration_user',
    :'migration_password'
)
\gexec

SELECT format(
    'CREATE ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'app_user',
    :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec
SELECT format(
    'ALTER ROLE %I WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'app_user',
    :'app_password'
)
\gexec

-- 既存のrole membershipを除去し、SET ROLEによる権限上昇も閉じる。
SELECT format('REVOKE %I FROM %I', granted.rolname, member.rolname)
FROM pg_auth_members AS membership
JOIN pg_roles AS granted ON granted.oid = membership.roleid
JOIN pg_roles AS member ON member.oid = membership.member
WHERE member.rolname IN (:'migration_user', :'app_user')
\gexec

SELECT format('ALTER DATABASE %I OWNER TO %I', :'app_db', :'migration_user')
\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM %I', :'app_db', :'app_user')
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'app_db', :'app_user')
\gexec
SQL

psql -v ON_ERROR_STOP=1 --host=db --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" <<'SQL'
\getenv migration_user POSTGRES_MIGRATION_USER
\getenv app_user POSTGRES_APP_USER

SELECT format('ALTER SCHEMA public OWNER TO %I', :'migration_user')
\gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

SELECT format(
    'ALTER %s %I.%I OWNER TO %I',
    CASE c.relkind
        WHEN 'S' THEN 'SEQUENCE'
        WHEN 'v' THEN 'VIEW'
        WHEN 'm' THEN 'MATERIALIZED VIEW'
        ELSE 'TABLE'
    END,
    n.nspname,
    c.relname,
    :'migration_user'
)
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'S', 'v', 'm')
ORDER BY c.relkind, c.relname
\gexec

SELECT format('REVOKE ALL ON SCHEMA public FROM %I', :'app_user')
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_user')
\gexec
SELECT format(
    'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I',
    :'app_user'
)
\gexec
SELECT format(
    'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I',
    :'app_user'
)
\gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
    :'migration_user',
    :'app_user'
)
\gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
    :'migration_user',
    :'app_user'
)
\gexec
SQL

attributes="$(
    psql -v ON_ERROR_STOP=1 --host=db --username="$POSTGRES_USER" --dbname=postgres --tuples-only --no-align <<'SQL'
\getenv migration_user POSTGRES_MIGRATION_USER
\getenv app_user POSTGRES_APP_USER
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls,
       EXISTS (SELECT 1 FROM pg_auth_members WHERE member = pg_roles.oid)
FROM pg_roles
WHERE rolname IN (:'migration_user', :'app_user')
ORDER BY rolname;
SQL
)"
unsafe="$(printf '%s\n' "$attributes" | awk -F'|' 'NF != 7 || $2 != "f" || $3 != "f" || $4 != "f" || $5 != "f" || $6 != "f" || $7 != "f" { print }')"
count="$(printf '%s\n' "$attributes" | awk 'NF { count += 1 } END { print count + 0 }')"
if [ "$count" != "2" ] || [ -n "$unsafe" ]; then
    echo "[db-role] migration/app roles are missing or still privileged" >&2
    exit 1
fi

echo "[db-role] migration owner and application DML role are provisioned separately"
