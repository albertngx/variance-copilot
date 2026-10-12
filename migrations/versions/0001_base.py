"""Extensions, schemas and roles (SPEC §7.10 section 0).

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from alembic import op

from variance_copilot.db.ddl import execute_script

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMAS = ("md", "fin", "rpt", "doc", "app", "audit", "review", "truth")

ROLES = {
    "vc_app": "API runtime (writes app, review, audit)",
    "vc_tool_reader": "tool execution (read-only, RLS)",
    "vc_auditor": "audit read access",
    "vc_eval": "evaluation runner (ground truth)",
}


def upgrade() -> None:
    bind = op.get_bind()
    execute_script(bind, "CREATE EXTENSION IF NOT EXISTS vector;")
    execute_script(bind, "\n".join(f"CREATE SCHEMA IF NOT EXISTS {s};" for s in SCHEMAS))
    for role, purpose in ROLES.items():
        execute_script(
            bind,
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE {role} NOLOGIN;
                END IF;
            END
            $$;
            COMMENT ON ROLE {role} IS '{purpose}';
            """,
        )


def downgrade() -> None:
    bind = op.get_bind()
    execute_script(bind, "\n".join(f"DROP SCHEMA IF EXISTS {s} CASCADE;" for s in SCHEMAS))
    for role in ROLES:
        execute_script(
            bind,
            f"""
            DO $$
            BEGIN
                DROP ROLE IF EXISTS {role};
            EXCEPTION WHEN dependent_objects_still_exist THEN
                RAISE NOTICE 'role {role} kept: still used by another database';
            END
            $$;
            """,
        )
    execute_script(bind, "DROP EXTENSION IF EXISTS vector;")
