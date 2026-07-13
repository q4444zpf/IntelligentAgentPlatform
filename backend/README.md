# Model provider API

```powershell
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

运行时配置默认保存在 SQLite 数据库 `data/model-providers.db`。可通过环境变量
`MODEL_PROVIDER_DATABASE` 指定其他数据库文件。内置供应商定义位于
`app/model_providers/registry.py`，用户密钥、额外模型、模型参数和默认模型写入数据库。

如果检测到旧版 `data/model-providers.json` 且数据库为空，启动时会自动导入，成功后
将旧文件改名为 `model-providers.json.migrated`。
