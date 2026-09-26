-- 0001_questions：题库表（Phase 3）
--
-- 用途：把**存量 phase2 库**升级到 phase3（`python3 hub.py upgrade-db`）。
-- 新建库走 schema/questions.sql；两份 DDL 必须保持一致，
-- 有测试断言「新库」与「迁移后的老库」结构相同。
-- 约定：迁移必须幂等、可重放（见本目录 README）。

CREATE TABLE IF NOT EXISTS questions (
    id           INTEGER PRIMARY KEY,
    question_key TEXT NOT NULL UNIQUE,
    qtype        TEXT NOT NULL CHECK (
        qtype IN ('choice', 'fill', 'calculation', 'experiment', 'other')
    ),
    stem         TEXT NOT NULL CHECK (length(trim(stem)) > 0),
    options_json TEXT,
    answer       TEXT NOT NULL CHECK (length(trim(answer)) > 0),
    analysis     TEXT,
    difficulty   INTEGER CHECK (difficulty IS NULL OR difficulty BETWEEN 1 AND 5),
    source_label TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (qtype <> 'choice' OR options_json IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_questions_qtype ON questions(qtype);

CREATE TABLE IF NOT EXISTS question_tags (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL CHECK (length(trim(tag)) > 0),
    PRIMARY KEY (question_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_question_tags_tag ON question_tags(tag);
