# Agent Run 审计回填

统一审计表升级后，由运维人员在 API 启动前或独立维护窗口显式执行：

```powershell
python -m alembic upgrade head
python -m app.audit.backfill
```

命令按批提交且可安全重跑。API 启动不会自动执行回填。
