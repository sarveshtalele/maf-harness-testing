# Python

- Framework: pytest unless the repository declares unittest or another runner. Parametrize with `@pytest.mark.parametrize` when one case has several variants.
- The file must contain a real, collectable top-level `def test_name(...):` with assertions on application results. An imports-only or constant-only file is invalid.
- Import roots: `generation_policy.python_import_roots` lists directories the runner adds to the import path; import a module under one by its bare module name (`skill-packages/03-x/mod.py` becomes `import mod`). Never use `sys.path` hacks. Import only names defined at module level; code that lives in a nested function is exercised through the enclosing function with collaborators patched.
- Use supplied signatures and source bodies. Never invent a `main()` or any function/attribute absent from the evidence.
- Check collaborator signatures and dataclass required fields before building fixtures; dictionary keys are not attributes; enum/tag strings must match source constants verbatim.
- Use `pathlib` for filesystem expectations (Windows-safe); import `unittest.mock` explicitly when used; use `tmp_path` for files.

## Integration tests

- Build the app via the repo factory with `tmp_path` data dirs; never import an app that reads real config.
- FastAPI: wrap `TestClient` in `with` so lifespan runs; override dependencies keyed on the exact callable in `Depends(...)`; call only routes present in the route source; seed through the app's public API or existing conftest fixtures.
- Flask uses `app.test_client()`, Django uses `TestCase`/`Client` (`@pytest.mark.django_db` with pytest-django). Mock only external network/cloud.

```python
def test_list_items(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    app.dependency_overrides[get_registry] = lambda: FakeRegistry()  # key = callable in Depends(get_registry)
    with TestClient(app) as client:
        assert client.get("/items").status_code == 200
```
