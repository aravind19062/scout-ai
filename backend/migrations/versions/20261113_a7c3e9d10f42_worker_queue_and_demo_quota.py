"""worker queue columns and demo client key

Revision ID: a7c3e9d10f42
Revises: e41800308bdd
Create Date: 2026-11-13 10:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "a7c3e9d10f42"
down_revision = "e41800308bdd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("research_jobs", sa.Column("worker_id", sa.String(length=100), nullable=True))
    op.add_column("research_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("research_jobs", sa.Column("run_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("research_jobs", sa.Column("client_key", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_research_jobs_client_key"), "research_jobs", ["client_key"], unique=False)
    # Partial index for the worker's claim query (oldest queued job first).
    op.create_index("ix_research_jobs_queued", "research_jobs", ["created_at"], unique=False,
                    postgresql_where=sa.text("status = 'queued'"))


def downgrade() -> None:
    op.drop_index("ix_research_jobs_queued", table_name="research_jobs")
    op.drop_index(op.f("ix_research_jobs_client_key"), table_name="research_jobs")
    op.drop_column("research_jobs", "client_key")
    op.drop_column("research_jobs", "run_attempts")
    op.drop_column("research_jobs", "heartbeat_at")
    op.drop_column("research_jobs", "worker_id")
