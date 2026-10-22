"""Upvotes on the people of /gtm-leaderboard.

`showcasepersonvote` is one upvote on one person of the page, cast without signing in: `person` is
that person's row id in the hand-kept media/showcase/world.md, so it is a string, not a foreign key;
`voter` is the random id in the voter's cookie and `address_hash` a keyed hash of their address. A
person takes one upvote per voter and one per address.

Revision ID: 0078
Revises: 0077
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0078"
down_revision: str | Sequence[str] | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "showcasepersonvote",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person", sa.String(length=64), nullable=False),
        sa.Column("voter", sa.String(length=64), nullable=False),
        sa.Column("address_hash", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("person", "voter", name="uq_showcasepersonvote_person_voter"),
        sa.UniqueConstraint("person", "address_hash", name="uq_showcasepersonvote_person_address"),
    )
    op.create_index("ix_showcasepersonvote_voter", "showcasepersonvote", ["voter"])


def downgrade() -> None:
    op.drop_index("ix_showcasepersonvote_voter", table_name="showcasepersonvote")
    op.drop_table("showcasepersonvote")
