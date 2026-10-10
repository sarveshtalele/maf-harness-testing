# Generation task: sqlite

Use disposable per-test SQLite files. Test the actual schema, unique transcript identity, empty summary and successful writes after failed duplicate insertion. Do not change target source or rely on global import-time configuration.

Generate a pytest suite importing from `target`. Available dependencies: Python standard library and pytest. Supply test_*.py and optional conftest.py only. The evaluator adds the workspace to PYTHONPATH.
