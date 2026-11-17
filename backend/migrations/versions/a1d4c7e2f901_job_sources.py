"""job_sources: shared job schema columns, source config, source refs, search runs, import history, audit

Revision ID: a1d4c7e2f901
Revises: 980297bb139c
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1d4c7e2f901"
down_revision: Union[str, Sequence[str], None] = "980297bb139c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, col in [("source_method", sa.String(24)), ("permission_status", sa.String(32)), ("state", sa.String(10)), ("postcode", sa.String(10)),
                      ("requirements", sa.Text()), ("closing_date", sa.Date()), ("application_url", sa.Text()), ("ats_provider", sa.String(30))]:
        op.add_column("listings", sa.Column(name, col, nullable=True))
    op.add_column("listings", sa.Column("salary_currency", sa.String(3), nullable=False, server_default="AUD"))
    op.create_table("source_configs",
        sa.Column("source", sa.String(30), primary_key=True), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_success_at", sa.DateTime(timezone=True)), sa.Column("last_error", sa.Text()), sa.Column("last_error_at", sa.DateTime(timezone=True)),
        sa.Column("last_link_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("source_refs",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(30), nullable=False), sa.Column("source_job_id", sa.String(200), nullable=False), sa.Column("url", sa.Text()),
        sa.Column("method", sa.String(24), nullable=False, server_default="api"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source", "source_job_id", name="uq_source_ref"))
    op.create_index("ix_source_refs_listing_id", "source_refs", ["listing_id"])
    op.create_table("search_runs",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("criteria", postgresql.JSONB(), nullable=False), sa.Column("results", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.create_table("import_history",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("source", sa.String(30), nullable=False), sa.Column("method", sa.String(24), nullable=False),
        sa.Column("url", sa.Text()), sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id", ondelete="SET NULL")), sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("detail", sa.Text()), sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("source_audit",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("source", sa.String(30), nullable=False), sa.Column("event", sa.String(40), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False), sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    # every stored listing already appeared on exactly one source: record that as its first reference
    op.execute("insert into source_refs (listing_id, source, source_job_id, url, method) select id, split_part(source, ':', 1), source_id, url, "
               "case when source like 'workable%' then 'feed' when source = 'web' then 'web_page' when source = 'manual' then 'manual_import' else 'api' end from listings "
               "on conflict do nothing")


def downgrade() -> None:
    for t in ("source_audit", "import_history", "search_runs", "source_refs", "source_configs"):
        op.drop_table(t)
    for c in ("source_method", "permission_status", "state", "postcode", "requirements", "closing_date", "application_url", "ats_provider", "salary_currency"):
        op.drop_column("listings", c)
