-- ============================================================
-- core：元信息、迁移记录、班级、学生
--
-- 约定：
-- - 时间列一律存 UTC（datetime('now')），展示时按 project.timezone 转；
-- - 学生一律用 id / student_uid 关联；姓名只用于显示，不参与匹配。
-- ============================================================

-- 1. 元信息：schema 版本、演示数据集版本等单值
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 2. 迁移记录：此后任何 schema 变更都走 schema/migrations/，这里记已应用的迁移
CREATE TABLE IF NOT EXISTS schema_migrations (
    name       TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 3. 班级
CREATE TABLE IF NOT EXISTS classes (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 4. 学生：student_uid 是数据集里的稳定标识，id 是库内主键
CREATE TABLE IF NOT EXISTS students (
    id          INTEGER PRIMARY KEY,
    student_uid TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    class_id    INTEGER NOT NULL REFERENCES classes(id),
    seat_no     INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_id);
