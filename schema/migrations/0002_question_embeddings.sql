-- 0002_question_embeddings：题目向量表（Phase 5 实验分支 pilot）
--
-- 用途：把**存量库**升级到含 question_embeddings（`python3 hub.py upgrade-db`）。
-- 新建库走 schema/questions.sql；两份 DDL 必须保持一致，
-- 有测试断言「新库」与「迁移后的老库」结构相同。
-- 约定：迁移必须幂等、可重放（见本目录 README）。
--
-- 设计：一题一行向量（question_id 主键）；换模型重跑向量时覆盖旧行，
-- 不保留历史。vector 存 float32 小端字节串，dims 记录维度以便校验。

CREATE TABLE IF NOT EXISTS question_embeddings (
    question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    model       TEXT NOT NULL CHECK (length(trim(model)) > 0),
    dims        INTEGER NOT NULL CHECK (dims > 0),
    vector      BLOB NOT NULL CHECK (length(vector) > 0),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
