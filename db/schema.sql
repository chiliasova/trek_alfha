-- Схема БД приложения (бот «Альфа-Курс помощник»).
-- Исходная выгрузка кейса восстанавливается в отдельную базу alfa_source,
-- а здесь живут только данные бота и индекс базы знаний.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------- Пользователи и профиль бизнеса ----------

CREATE TABLE IF NOT EXISTS users (
    id            BIGINT PRIMARY KEY,              -- Telegram user id
    username      TEXT,
    first_name    TEXT,
    timezone      TEXT        NOT NULL DEFAULT 'Europe/Moscow',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS business_profiles (
    user_id      BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    stage        TEXT,     -- idea | launch | running | growth
    industry     TEXT,     -- свободный текст или значение из справочника отраслей
    tax_regime   TEXT,     -- usn6 | usn15 | patent | npd | osno | unknown
    legal_form   TEXT,     -- ip | ooo | self_employed | none
    has_staff    BOOLEAN,
    current_goal TEXT,     -- что сейчас решает пользователь
    experience   TEXT,     -- beginner | intermediate | advanced
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- База знаний (индекс для RAG) ----------

CREATE TABLE IF NOT EXISTS kb_documents (
    id            SERIAL PRIMARY KEY,
    source_table  TEXT    NOT NULL,   -- Articles | Lessons | Documents | Glossary | Courses ...
    source_id     TEXT    NOT NULL,   -- id записи в исходной выгрузке
    content_type  TEXT    NOT NULL,   -- article | lesson | document | glossary | course | webinar | podcast
    title         TEXT    NOT NULL,
    description   TEXT,
    url           TEXT,               -- ссылка на материал на платформе (только из выгрузки)
    is_paid       BOOLEAN NOT NULL DEFAULT FALSE,
    topics        TEXT[]  NOT NULL DEFAULT '{}',
    UNIQUE (source_table, source_id)
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id           SERIAL PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL,
    text         TEXT    NOT NULL,
    embedding    vector,              -- размерность задаётся моделью (bge-m3: 1024)
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('russian', text)) STORED,
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS kb_chunks_tsv_idx ON kb_chunks USING GIN (tsv);

-- Служебное: какой моделью посчитаны эмбеддинги
CREATE TABLE IF NOT EXISTS kb_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------- Диалоги ----------

CREATE TABLE IF NOT EXISTS dialog_messages (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT        NOT NULL,          -- user | assistant
    text        TEXT        NOT NULL,
    answer_type TEXT,                          -- answer | clarify | not_found (для ответов бота)
    sources     JSONB       NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dialog_messages_user_idx ON dialog_messages (user_id, created_at DESC);

-- ---------- Напоминания ----------

CREATE TABLE IF NOT EXISTS reminders (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind             TEXT        NOT NULL,     -- learning | tax | custom
    text             TEXT        NOT NULL,
    next_run_at      TIMESTAMPTZ NOT NULL,
    repeat_interval  INTERVAL,                 -- NULL = разовое
    is_active        BOOLEAN     NOT NULL DEFAULT TRUE,
    related_doc_id   INTEGER REFERENCES kb_documents(id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_sent_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS reminders_due_idx ON reminders (next_run_at) WHERE is_active;

-- ---------- События (метрика возврата на платформу) ----------

CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT      REFERENCES users(id) ON DELETE CASCADE,
    type        TEXT        NOT NULL,  -- question | answer_shown | link_click | reminder_sent | reminder_open | profile_update
    payload     JSONB       NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_user_type_idx ON events (user_id, type, created_at);
