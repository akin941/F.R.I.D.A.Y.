"""The memory eval set (Phase 8B branch 3, brief D9).

A measuring instrument, not product code: no production module imports
this package (`memory/tests/test_evals.py` checks it; import-linter checks
the other apps). Its only entry points are `manage.py memory_eval` and the
read-only `GET /api/v1/memory/evals/runs` (metrics only).

- `schema`: case format v1, validated strictly (bounds, unknown keys).
- `loader`: the committed public seed and the owner's private directory.
- `world`: the eval workspaces and the per-case setup rows.
- `retrieval`: the retrieval modes under test (`legacy` today).
- `metrics`: recall@10, leak rate, temporal correctness, answer accuracy.
- `runner`: one run over a case set, the `learning_eval` cap, result files.
- `results`: the metrics-only result-file format `record` accepts.
"""
