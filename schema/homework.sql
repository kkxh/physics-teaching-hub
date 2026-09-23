-- ============================================================
-- homework：作业布置、提交状态、订正记录
--
-- 口径约定（按班级统计完成率时）：分子与分母必须同时限定班级
-- （学生当前班级 = 作业所属班级）。只限定分母会把转班学生的历史提交
-- 算进新班级，完成率可能超过 100%。
-- ============================================================

-- 1. 作业布置：一次布置属于一个班
CREATE TABLE IF NOT EXISTS homework_assignments (
    id            INTEGER PRIMARY KEY,
    assign_key    TEXT NOT NULL UNIQUE,
    class_id      INTEGER NOT NULL REFERENCES classes(id),
    topic         TEXT NOT NULL,
    assigned_date TEXT NOT NULL,
    due_date      TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 2. 提交状态：一个学生一份作业一条
CREATE TABLE IF NOT EXISTS homework_submissions (
    id            INTEGER PRIMARY KEY,
    assignment_id INTEGER NOT NULL REFERENCES homework_assignments(id),
    student_id    INTEGER NOT NULL REFERENCES students(id),
    status        TEXT NOT NULL CHECK (status IN ('submitted', 'late', 'missing')),
    submitted_at  TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (assignment_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_submissions_assignment ON homework_submissions(assignment_id);
CREATE INDEX IF NOT EXISTS idx_submissions_student ON homework_submissions(student_id);
-- 按班级统计完成率要按 class_id 过滤
CREATE INDEX IF NOT EXISTS idx_assignments_class ON homework_assignments(class_id);

-- 3. 订正记录：一份提交最多一条订正记录
CREATE TABLE IF NOT EXISTS corrections (
    id            INTEGER PRIMARY KEY,
    submission_id INTEGER NOT NULL REFERENCES homework_submissions(id),
    corrected_at  TEXT,
    note          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (submission_id)
);
