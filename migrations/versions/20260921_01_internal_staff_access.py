"""Migrate the legacy mixed user model to internal staff-patient access control."""

from alembic import op
import sqlalchemy as sa


revision = "20260921_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if not {"users", "patient_profiles"}.issubset(tables):
        # Empty installations are created from current SQLAlchemy metadata when the API starts.
        return

    if "patient_access" not in tables:
        op.create_table(
            "patient_access",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("patient_id", sa.String(length=64), nullable=False),
            sa.Column("granted_by", sa.String(length=64), nullable=False, server_default="legacy_migration"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["patient_id"], ["patient_profiles.patient_id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "patient_id", name="uq_patient_access_user_patient"),
        )
        op.create_index("ix_patient_access_user_id", "patient_access", ["user_id"])
        op.create_index("ix_patient_access_patient_id", "patient_access", ["patient_id"])
        op.execute(sa.text(
            "INSERT INTO patient_access (user_id, patient_id, granted_by) "
            "SELECT user_id, patient_id, 'legacy_migration' FROM users "
            "WHERE role IN ('doctor', 'pharmacist') AND patient_id IS NOT NULL"
        ))

    columns = {column["name"]: column for column in inspector.get_columns("users")}
    patient_id = columns.get("patient_id")
    if patient_id and not patient_id.get("nullable", True):
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("patient_id", existing_type=sa.String(length=64), nullable=True)
    op.execute(sa.text("UPDATE users SET active = 0 WHERE role = 'patient'"))


def downgrade() -> None:
    bind = op.get_bind()
    if "patient_access" in sa.inspect(bind).get_table_names():
        op.drop_index("ix_patient_access_patient_id", table_name="patient_access")
        op.drop_index("ix_patient_access_user_id", table_name="patient_access")
        op.drop_table("patient_access")
