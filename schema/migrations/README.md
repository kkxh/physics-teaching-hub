# schema/migrations/：schema 变更记录

Phase 2 起，**任何 schema 变更都走这里**，不再改 `schema/*.sql` 的建表语句（那些文件只负责建新库）。

两份文件的关系：`schema/migrations/000N_*.sql` 负责把**存量库**升级到新版本，
`schema/*.sql` 里的对应文件负责建**新库**；同一个结构改动要同时写两处，DDL 必须一致
（有测试断言「新库」与「迁移后的老库」结构相同）。

## 约定

- 文件名形如 `0001_说明.sql`，按名字排序执行；
- 每个迁移文件必须**幂等、可重放**：重复执行不报错、不产生重复数据；
- 已应用的迁移记在 `schema_migrations(name, applied_at)` 表里；`init_db.py` 只执行没记录过的；
- 一个迁移只做一件事，别把多个模块的改动塞进同一个文件；
- 迁移文件随 `init_db.py` 一起发布，不能回改已发布的内容——要改就再加一个。

## 怎么升级存量库

```bash
python3 hub.py upgrade-db          # 只应用迁移 + 校验表齐全，不导入任何数据
```

版本语义（`db.py`）：

- `meta.schema_version` 等于当前版本：直接用，命令前会校验表是否齐全；
- 已知旧版本（`db.MIGRATABLE_SCHEMA_VERSIONS`，例如 `phase2`）：`upgrade-db` 跑迁移、
  校验通过后才写新版本号，**不删库、不丢数据**；其它命令会提示先跑 `upgrade-db`；
- `phase1-temp` 与未知版本：只给重建这一条路（`init-db --demo --rebuild --yes`）。

## 现状

| 文件 | 内容 | 日期 |
| --- | --- | --- |
| `0001_questions.sql` | 题库表（`questions` / `question_tags`），Phase 3 P3.0 | 2026-09-26 |
