-- ============================================================
-- questions：题目框架（Phase 3 起）
--
-- 约定：
-- - 题目内容是**使用者自己的数据**：真实题库只落在使用者本机（data/ 已被 .gitignore 拒绝）；
--   仓库里唯一的题目是自制示例题（examples/），不得引入第三方试卷、教辅与教材原文；
-- - 题目域与成绩/作业/画像域松耦合：只靠 question_key 与知识点标签关联，不建跨域外键；
-- - 时间列一律存 UTC（datetime('now')），展示时按 project.timezone 转。
-- ============================================================

-- 1. 题目
--    difficulty 允许 NULL：语义是「未标难度」，不是 0（判空用 is None，见 NOTES 第 6 条）。
--    choice 题必须带 options_json（选项数组的 JSON）。
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

-- 2. 知识点标签：与错因标签（error_tags）是两套词典，映射走配置
CREATE TABLE IF NOT EXISTS question_tags (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL CHECK (length(trim(tag)) > 0),
    PRIMARY KEY (question_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_question_tags_tag ON question_tags(tag);

-- 3. 题目向量（Phase 5 实验分支 pilot）：一题一行，供语义相似检索。
--    与 schema/migrations/0002_question_embeddings.sql 的 DDL 保持一致。
CREATE TABLE IF NOT EXISTS question_embeddings (
    question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    model       TEXT NOT NULL CHECK (length(trim(model)) > 0),
    dims        INTEGER NOT NULL CHECK (dims > 0),
    vector      BLOB NOT NULL CHECK (length(vector) > 0),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
