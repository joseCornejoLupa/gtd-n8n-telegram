"""closed_from: estado previo de una tarea completada o descartada

Permite que "reabrir" restaure exactamente el estado anterior (una tarea de 'algún día'
descartada por error vuelve a 'algún día', no a 'siguiente acción').

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tasks ADD COLUMN closed_from text")
    op.execute("""
    ALTER TABLE tasks ADD CONSTRAINT ck_tasks_closed_from CHECK (
      closed_from IS NULL
      OR closed_from IN ('inbox','next','waiting','someday','reference'))""")


def downgrade() -> None:
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_closed_from")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS closed_from")
