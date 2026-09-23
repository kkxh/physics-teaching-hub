-- ============================================================
-- scores：考试、成绩，以及小题级得分（P2.7 启用小题表）
-- ============================================================

-- 1. 考试
CREATE TABLE IF NOT EXISTS exams (
    id         INTEGER PRIMARY KEY,
    exam_key   TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    exam_date  TEXT NOT NULL,
    full_score REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 2. 总分：一个学生一场考试一条
CREATE TABLE IF NOT EXISTS exam_scores (
    id         INTEGER PRIMARY KEY,
    exam_id    INTEGER NOT NULL REFERENCES exams(id),
    student_id INTEGER NOT NULL REFERENCES students(id),
    score      REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (exam_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_exam_scores_exam ON exam_scores(exam_id);
CREATE INDEX IF NOT EXISTS idx_exam_scores_student ON exam_scores(student_id);

-- 3. 小题（P2.7 考试链路启用；此前不写入）
--    item_no 用 TEXT：题号可能是 "12"、"12(1)" 这类写法
CREATE TABLE IF NOT EXISTS exam_items (
    id         INTEGER PRIMARY KEY,
    exam_id    INTEGER NOT NULL REFERENCES exams(id),
    item_no    TEXT NOT NULL,
    full_score REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (exam_id, item_no)
);

-- 4. 小题得分
CREATE TABLE IF NOT EXISTS item_scores (
    id         INTEGER PRIMARY KEY,
    item_id    INTEGER NOT NULL REFERENCES exam_items(id),
    student_id INTEGER NOT NULL REFERENCES students(id),
    score      REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (item_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_item_scores_item ON item_scores(item_id);
