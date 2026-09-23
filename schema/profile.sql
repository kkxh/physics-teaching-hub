-- ============================================================
-- profile：学生能力画像（计算结果物化，可重算、幂等）
-- ============================================================

CREATE TABLE IF NOT EXISTS ability_scores (
    id          INTEGER PRIMARY KEY,
    student_id  INTEGER NOT NULL REFERENCES students(id),
    dimension   TEXT NOT NULL,
    score       REAL NOT NULL,
    computed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (student_id, dimension)
);

CREATE INDEX IF NOT EXISTS idx_ability_scores_student ON ability_scores(student_id);
