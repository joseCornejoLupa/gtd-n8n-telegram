"""esquema inicial GTD

Revision ID: 0001
Revises:
Create Date: 2026-10-23
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE users (
      id               bigint GENERATED ALWAYS AS IDENTITY,
      telegram_user_id bigint NOT NULL,
      timezone         text NOT NULL DEFAULT 'America/Lima',
      created_at       timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT pk_users PRIMARY KEY (id),
      CONSTRAINT uq_users_telegram_user_id UNIQUE (telegram_user_id)
    )""")

    op.execute("""
    CREATE TABLE contexts (
      id      bigint GENERATED ALWAYS AS IDENTITY,
      user_id bigint NOT NULL,
      name    text NOT NULL,
      CONSTRAINT pk_contexts PRIMARY KEY (id),
      CONSTRAINT fk_contexts_user_id_users FOREIGN KEY (user_id) REFERENCES users (id),
      CONSTRAINT uq_contexts_user_id UNIQUE (user_id, name)
    )""")

    op.execute("""
    CREATE TABLE projects (
      id             bigint GENERATED ALWAYS AS IDENTITY,
      user_id        bigint NOT NULL,
      title          text NOT NULL,
      outcome        text,
      status         text NOT NULL DEFAULT 'active',
      sequential     boolean NOT NULL DEFAULT false,
      origin_task_id bigint,
      created_at     timestamptz NOT NULL DEFAULT now(),
      completed_at   timestamptz,
      CONSTRAINT pk_projects PRIMARY KEY (id),
      CONSTRAINT fk_projects_user_id_users FOREIGN KEY (user_id) REFERENCES users (id),
      CONSTRAINT ck_projects_status
        CHECK (status IN ('active','someday','done','dropped'))
    )""")

    op.execute("""
    CREATE TABLE tasks (
      id            bigint GENERATED ALWAYS AS IDENTITY,
      user_id       bigint NOT NULL,
      raw_text      text NOT NULL,
      title         text,
      notes         text,
      status        text NOT NULL DEFAULT 'inbox',
      project_id    bigint,
      position      integer,
      context_id    bigint,
      waiting_on    text,
      waiting_since timestamptz,
      due_at        timestamptz,
      defer_until   timestamptz,
      source        text NOT NULL DEFAULT 'telegram',
      source_ref    text,
      captured_at   timestamptz NOT NULL DEFAULT now(),
      clarified_at  timestamptz,
      completed_at  timestamptz,
      CONSTRAINT pk_tasks PRIMARY KEY (id),
      CONSTRAINT fk_tasks_user_id_users FOREIGN KEY (user_id) REFERENCES users (id),
      CONSTRAINT fk_tasks_project_id_projects FOREIGN KEY (project_id) REFERENCES projects (id),
      CONSTRAINT fk_tasks_context_id_contexts FOREIGN KEY (context_id) REFERENCES contexts (id),
      CONSTRAINT ck_tasks_status CHECK (status IN
        ('inbox','next','waiting','someday','reference','done','trashed','converted')),
      CONSTRAINT ck_tasks_next_has_title CHECK (status <> 'next' OR title IS NOT NULL),
      CONSTRAINT ck_tasks_waiting_has_waiting_on
        CHECK (status <> 'waiting' OR waiting_on IS NOT NULL),
      CONSTRAINT uq_tasks_user_id UNIQUE (user_id, source, source_ref),
      CONSTRAINT uq_tasks_project_id UNIQUE (project_id, position)
        DEFERRABLE INITIALLY DEFERRED
    )""")

    op.execute("""
    ALTER TABLE projects
      ADD CONSTRAINT fk_projects_origin_task_id_tasks
      FOREIGN KEY (origin_task_id) REFERENCES tasks (id)""")

    op.execute("""
    CREATE INDEX tasks_inbox ON tasks (user_id, captured_at) WHERE status = 'inbox'""")
    op.execute("""
    CREATE INDEX tasks_next ON tasks (user_id, context_id) WHERE status = 'next'""")
    op.execute("""
    CREATE INDEX tasks_wait ON tasks (user_id, waiting_since) WHERE status = 'waiting'""")
    op.execute("CREATE INDEX tasks_proj ON tasks (project_id, position)")

    op.execute("""
    CREATE TABLE reviews (
      id       bigint GENERATED ALWAYS AS IDENTITY,
      user_id  bigint NOT NULL,
      done_at  timestamptz NOT NULL DEFAULT now(),
      snapshot jsonb NOT NULL,
      CONSTRAINT pk_reviews PRIMARY KEY (id),
      CONSTRAINT fk_reviews_user_id_users FOREIGN KEY (user_id) REFERENCES users (id)
    )""")

    # Lo que realmente se puede hacer ahora. Columnas explícitas a propósito: un
    # `t.*` se congela al crear la vista y esconde columnas añadidas después.
    # Si cambian las columnas de `tasks`, la migración correspondiente debe recrearla.
    op.execute("""
    CREATE VIEW available_tasks AS
    SELECT t.id, t.user_id, t.raw_text, t.title, t.notes, t.status, t.project_id,
           t.position, t.context_id, t.waiting_on, t.waiting_since, t.due_at,
           t.defer_until, t.source, t.source_ref, t.captured_at, t.clarified_at,
           t.completed_at
    FROM tasks t
    LEFT JOIN projects p ON p.id = t.project_id
    WHERE t.status = 'next'
      AND (t.defer_until IS NULL OR t.defer_until <= now())
      AND (p.id IS NULL OR p.status = 'active')
      AND (p.sequential IS NOT TRUE OR t.position = (
            SELECT min(t2.position) FROM tasks t2
            WHERE t2.project_id = t.project_id
              AND t2.status IN ('next', 'waiting')))
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS available_tasks")
    op.execute("DROP TABLE IF EXISTS reviews")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS fk_projects_origin_task_id_tasks")
    op.execute("DROP TABLE IF EXISTS tasks")
    op.execute("DROP TABLE IF EXISTS projects")
    op.execute("DROP TABLE IF EXISTS contexts")
    op.execute("DROP TABLE IF EXISTS users")
