-- ============================================================
-- Phase 1 临时 schema —— **不是最终数据模型**
--
-- 这些表只为跑通「建库 → 导入成绩 → 生成报告」这条最小闭环。
-- Phase 2 会用正式的模块化 schema 替换或扩展它：
--   表名、字段名、约束都不承诺兼容，请勿据此做长期集成或数据迁移。
-- 数据库里 meta.schema_version 会记成 'phase1-temp'，便于将来识别。
--
-- 约定：
-- - 时间列一律存 UTC（datetime('now')），展示时再按 project.timezone 转；
-- - 学生一律用 id / student_uid 关联，姓名只用于显示，不参与匹配。
-- ============================================================

-- 1. 元信息：schema 版本与演示数据标记
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 2. 班级（Phase 1 只存名字）
CREATE TABLE IF NOT EXISTS classes (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 3. 学生：student_uid 是演示数据集里的稳定标识，id 是库内主键
CREATE TABLE IF NOT EXISTS students (
    id          INTEGER PRIMARY KEY,
    student_uid TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    class_id    INTEGER NOT NULL REFERENCES classes(id),
    seat_no     INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 4. 考试
CREATE TABLE IF NOT EXISTS exams (
    id         INTEGER PRIMARY KEY,
    exam_key   TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    exam_date  TEXT NOT NULL,
    full_score REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 5. 成绩：一个学生一场考试一条
CREATE TABLE IF NOT EXISTS exam_scores (
    id         INTEGER PRIMARY KEY,
    exam_id    INTEGER NOT NULL REFERENCES exams(id),
    student_id INTEGER NOT NULL REFERENCES students(id),
    score      REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (exam_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_id);
CREATE INDEX IF NOT EXISTS idx_exam_scores_exam ON exam_scores(exam_id);
