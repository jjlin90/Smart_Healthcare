"""Durable reservation for explicitly approved tool writes."""

from alembic import op
from backend.models import ToolExecution

revision = "20260926_02"
down_revision = "20260921_01"
branch_labels = None
depends_on = None


def upgrade():
    ToolExecution.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    ToolExecution.__table__.drop(op.get_bind(), checkfirst=True)
