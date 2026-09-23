# schema/migrations/：schema 变更记录

Phase 2 起，**任何 schema 变更都走这里**，不再改 `schema/*.sql` 的建表语句（那些文件只负责建新库）。

## 约定

- 文件名形如 `0001_说明.sql`，按名字排序执行；
- 每个迁移文件必须**幂等、可重放**：重复执行不报错、不产生重复数据；
- 已应用的迁移记在 `schema_migrations(name, applied_at)` 表里；`init_db.py` 只执行没记录过的；
- 一个迁移只做一件事，别把多个模块的改动塞进同一个文件；
- 迁移文件随 `init_db.py` 一起发布，不能回改已发布的内容——要改就再加一个。

## 现状

目录暂时为空：Phase 2 的正式 schema 由 `schema/*.sql` 一次建好。第一次真正的结构变更是
Phase 2 之后的模块改动，届时从这里开始。
