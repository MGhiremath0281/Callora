"""Module 5 (Priority Scoring) — priority columns on recovery_cases.

Revision ID: 0004_priority
Revises: 0003_recovery
Create Date: 2026-10-20

Adds the Module 5 ranking surface to the existing recovery_cases table.
The existing ``score`` column (Module 3's 0..1 recoverability score) is
deliberately untouched — priority uses its own normalized 0..100 column
so no existing meaning changes. All new columns are nullable: rows
created before this migration simply stay unscored until the next sweep
scores them (no backfill is fabricated).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_priority"
down_revision: Union[str, None] = "0003_recovery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recovery_cases",
        sa.Column("priority_score", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "recovery_cases",
        sa.Column("priority_rank", sa.Integer(), nullable=True),
    )
    op.add_column(
        "recovery_cases",
        sa.Column("priority_level", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "recovery_cases",
        sa.Column("score_breakdown", sa.JSON(), nullable=True),
    )
    op.add_column(
        "recovery_cases",
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_recovery_cases_business_priority",
        "recovery_cases",
        ["business_id", "priority_score"],
    )


def downgrade() -> None:
    op.drop_index("ix_recovery_cases_business_priority", table_name="recovery_cases")
    op.drop_column("recovery_cases", "scored_at")
    op.drop_column("recovery_cases", "score_breakdown")
    op.drop_column("recovery_cases", "priority_level")
    op.drop_column("recovery_cases", "priority_rank")
    op.drop_column("recovery_cases", "priority_score")
