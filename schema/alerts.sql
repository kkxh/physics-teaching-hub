-- ============================================================
-- alerts：预警与跟进闭环
--
-- 约定：解决一条预警必须留下跟进记录（谁、何时、怎么跟进、结果如何），
-- 不允许「静默解决」。
-- ============================================================

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY,
    student_id  INTEGER NOT NULL REFERENCES students(id),
    kind        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_student ON alerts(student_id);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
-- 扫描时按（学生, 规则, 状态）找未解决预警
CREATE INDEX IF NOT EXISTS idx_alerts_student_kind ON alerts(student_id, kind, status);

CREATE TABLE IF NOT EXISTS follow_ups (
    id          INTEGER PRIMARY KEY,
    alert_id    INTEGER NOT NULL REFERENCES alerts(id),
    note        TEXT NOT NULL,
    outcome     TEXT,
    recorded_by TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_follow_ups_alert ON follow_ups(alert_id);
