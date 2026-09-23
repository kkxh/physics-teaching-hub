-- ============================================================
-- errors：错因标签、错因记录、行为记录
--
-- 约定：错因记录必须挂在一场考试或一份作业上（CHECK 约束），
-- 写入前还要显式校验被关联的行确实存在。
-- ============================================================

-- 1. 错因标签字典：code 是稳定标识，label 是显示名
CREATE TABLE IF NOT EXISTS error_tags (
    id         INTEGER PRIMARY KEY,
    code       TEXT NOT NULL UNIQUE,
    label      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 2. 错因记录
CREATE TABLE IF NOT EXISTS error_records (
    id            INTEGER PRIMARY KEY,
    student_id    INTEGER NOT NULL REFERENCES students(id),
    exam_id       INTEGER REFERENCES exams(id),
    assignment_id INTEGER REFERENCES homework_assignments(id),
    tag_id        INTEGER NOT NULL REFERENCES error_tags(id),
    note          TEXT,
    recorded_at   TEXT NOT NULL DEFAULT (datetime('now')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (exam_id IS NOT NULL OR assignment_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_error_records_student ON error_records(student_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_error_records_tag ON error_records(tag_id);

-- 3. 行为记录（课堂/课后观察）
CREATE TABLE IF NOT EXISTS behavior_records (
    id          INTEGER PRIMARY KEY,
    student_id  INTEGER NOT NULL REFERENCES students(id),
    kind        TEXT NOT NULL,
    detail      TEXT,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_behavior_records_student ON behavior_records(student_id, recorded_at);
