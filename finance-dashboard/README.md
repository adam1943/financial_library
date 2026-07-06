# Finance Dashboard

Local visual workspace for `knowledge_base`.

Run:

```bash
python3 finance-dashboard/server.py --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765
```

The dashboard reads `knowledge_base/candidates.csv`, `knowledge_base/latest.md`, `knowledge_base/config.json`, and `knowledge_base/data/finance_kb.sqlite`. The update buttons call the existing `finance-knowledge-updater/scripts/update_knowledge_base.py` script.
