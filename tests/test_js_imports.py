"""JavaScript/TypeScript import gate: undeclared packages, unresolvable paths, aliases and the jest environment."""

from __future__ import annotations

import json

import pytest

from maf_test_harness.skills.contracts import check_js_imports
from maf_test_harness.tools.code_intel import RepoContext, static_gate

PACKAGE = {
    "name": "web",
    "dependencies": {"react": "^18.3.1", "@acme/ui": "^1.0.0"},
    "devDependencies": {"jest": "^29.7.0", "ts-jest": "^29.1.5", "@types/jest": "^29.5.12"},
}
JEST = "const config = {\n  testEnvironment: 'node',\n  moduleNameMapper: {\n    '^@/(.*)$': '<rootDir>/src/$1',\n  },\n}\nexport default config\n"


@pytest.fixture
def web(tmp_path):
    files = {
        "web/package.json": json.dumps(PACKAGE),
        "web/jest.config.ts": JEST,
        "web/tsconfig.json": json.dumps({"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}),
        "web/src/lib/x.ts": "export const x = 1\n",
        "web/src/util/index.ts": "export const y = 2\n",
        "web/src/components/Button.tsx": "export const Button = () => null\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def _check(root, code, path="web/tests/a.test.ts"):
    ctx = RepoContext(
        root=root,
        source_files=["web/src/lib/x.ts", "web/src/util/index.ts", "web/src/components/Button.tsx"],
        package_files=["web/package.json"],
    )
    check_js_imports(code, path, [*ctx.source_files, *ctx.package_files], read_source=ctx.read)


def test_undeclared_testing_library_is_rejected_with_the_declared_list(web):
    with pytest.raises(
        ValueError, match="'@testing-library/react'.*not declared in web/package.json.*declared here: "
    ):
        _check(web, "import { render } from '@testing-library/react'\ntest('a', () => {})\n")
    with pytest.raises(ValueError, match="declared here: @types/jest, jest, ts-jest"):
        _check(web, "import { render } from '@testing-library/react'\n")


def test_declared_packages_and_node_builtins_are_accepted(web):
    _check(web, "import React from 'react'\nimport { join } from 'node:path'\nimport fs from 'fs/promises'\n")
    _check(web, "import { Widget } from '@acme/ui'\nconst os = require('os')\n")


def test_scoped_package_name_is_matched_on_scope_and_name(web):
    _check(web, "import x from '@acme/ui/theme/dark'\n")
    with pytest.raises(ValueError, match="import '@acme/other' in"):
        _check(web, "import x from '@acme/other/deep/path'\n")


def test_relative_import_resolves_with_extension_and_index(web):
    _check(
        web,
        "import { x } from '../src/lib/x'\nimport { y } from '../src/util'\nimport { Button } from '../src/components/Button'\n",
    )
    _check(web, "import { y } from '../src/util/index.js'\n")  # TypeScript ESM spelling of a .ts file
    with pytest.raises(
        ValueError,
        match="relative import '../src/missing' in web/tests/a.test.ts does not match any repository file",
    ):
        _check(web, "import { m } from '../src/missing'\n")


def test_tsconfig_alias_is_accepted_when_the_target_exists_and_rejected_otherwise(web):
    _check(web, "import { x } from '@/lib/x'\n")
    with pytest.raises(
        ValueError, match="alias '@/lib/nope' in web/tests/a.test.ts does not match any repository file"
    ):
        _check(web, "import { n } from '@/lib/nope'\n")


def test_jest_module_name_mapper_alias_is_accepted_and_rejected(web):
    (web / "web/tsconfig.json").unlink()  # only jest knows the alias here
    _check(web, "import { Button } from '@/components/Button'\n")
    with pytest.raises(ValueError, match="does not match any repository file"):
        _check(web, "import { z } from '@/components/Missing'\n")


def test_unparseable_alias_config_is_accepted_rather_than_guessed(web):
    (web / "web/tsconfig.json").write_text("{ this is not json", encoding="utf-8")
    _check(web, "import { n } from '@/lib/anything-at-all'\n")


def test_jest_mock_of_an_undeclared_package_is_rejected(web):
    with pytest.raises(ValueError, match="import 'left-pad' in web/tests/a.test.ts is not declared"):
        _check(web, "jest.mock('left-pad', () => ({}))\n")
    _check(web, "jest.mock('react', () => ({}))\n")


def test_commented_imports_are_ignored_and_glob_strings_are_kept(web):
    _check(
        web,
        "// import { render } from '@testing-library/react'\n/* import x from 'lodash' */\nconst g = 'src/**/*.ts'\n",
    )


def test_dom_use_under_node_test_environment_is_rejected(web):
    with pytest.raises(ValueError, match="testEnvironment is 'node' \\(no DOM\\)"):
        _check(web, "test('a', () => { expect(document.title).toBe('') })\n")


def test_jsdom_docblock_needs_the_jsdom_environment_package(web):
    code = (
        "/** @jest-environment jsdom */\ntest('a', () => { expect(window.innerWidth).toBeGreaterThan(0) })\n"
    )
    with pytest.raises(ValueError, match="jest-environment-jsdom is not declared"):
        _check(web, code)
    package = {
        **PACKAGE,
        "devDependencies": {**PACKAGE["devDependencies"], "jest-environment-jsdom": "^29.7.0"},
    }
    (web / "web/package.json").write_text(json.dumps(package), encoding="utf-8")
    _check(web, code)  # the docblock selects a declared environment, so DOM use is allowed


def test_type_only_import_of_a_declared_types_package_is_accepted(web):
    _check(web, "import type { Config } from 'jest'\n", path="web/jest.config.ts")
    with pytest.raises(ValueError, match="import 'jest-circus' in"):
        _check(web, "import { x } from 'jest-circus'\n")


def test_playwright_specs_are_provisioned_by_the_harness(web):
    _check(
        web, "import { test, expect } from '@playwright/test'\ntest('a', async () => { expect(1).toBe(1) })\n"
    )


def test_static_gate_applies_the_js_check_to_a_generated_tsx_file(web):
    ctx = RepoContext(
        root=web, source_files=["web/src/components/Button.tsx"], package_files=["web/package.json"]
    )
    code = "import { render } from '@testing-library/react'\n\ntest('CASE_ID: x', () => { render(null) })\n"
    with pytest.raises(ValueError, match="'@testing-library/react' in web/tests/a.test.tsx is not declared"):
        static_gate(ctx, "web/tests/a.test.tsx", code)
    static_gate(
        ctx,
        "web/tests/a.test.tsx",
        "import { Button } from '../src/components/Button'\n\ntest('a', () => { expect(Button).toBeDefined() })\n",
    )


def _repo(root, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text if isinstance(text, str) else json.dumps(text), encoding="utf-8")
    return root


def _run(root, code, path="web/tests/a.test.ts"):
    ctx = RepoContext(root=root, source_files=[], package_files=[])
    files = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    check_js_imports(code, path, files, read_source=ctx.read)


def _web_files(**extra):
    files = {
        "web/package.json": PACKAGE,
        "web/jest.config.ts": JEST,
        "web/tsconfig.json": {"compilerOptions": {"paths": {"@/*": ["./src/*"]}}},
        "web/src/lib/x.ts": "export const x = 1\n",
        "web/src/components/Button.tsx": "export const Button = () => null\n",
    }
    files.update(extra)
    return files


def test_hoisted_workspace_name_and_bundled_dependencies_are_declared(tmp_path):
    root = _repo(
        tmp_path,
        _web_files(
            **{
                "package.json": {"devDependencies": {"lodash-es": "^4.0.0"}},
                "web/package.json": {"name": "@acme/web", "bundledDependencies": ["left-pad"]},
                "packages/ui/package.json": {"name": "@acme/ui-kit"},
            }
        ),
    )
    _run(
        root,
        "import a from 'lodash-es'\nimport b from 'left-pad'\nimport c from '@acme/ui-kit'\nimport d from '@acme/web/x'\n",
    )
    with pytest.raises(ValueError, match="import 'not-declared-anywhere' in"):
        _run(root, "import z from 'not-declared-anywhere'\n")


def test_jest_vitest_and_types_declared_at_the_root_are_accepted(tmp_path):
    root = _repo(
        tmp_path,
        _web_files(
            **{
                "package.json": {
                    "devDependencies": {"jest": "^29.7.0", "vitest": "^1.0.0", "@types/shapes-lib": "1"}
                }
            }
        ),
    )
    _run(
        root,
        "import { jest as j } from '@jest/globals'\nimport { vi } from 'vitest'\nimport { defineConfig } from 'vitest/config'\n"
        "import type { Shape } from 'shapes-lib'\n",
    )
    with pytest.raises(ValueError, match="import 'shapes-lib' in"):
        _run(root, "import { make } from 'shapes-lib'\n")


@pytest.mark.parametrize(
    "variant",
    [
        {"web/jest.config.ts": "export default { projects: [{ testEnvironment: 'node' }] }\n"},
        {
            "web/jest.config.ts": "export default { testEnvironment: 'node', overrides: { testEnvironment: 'jsdom' } }\n"
        },
        {
            "web/package.json": {
                **PACKAGE,
                "devDependencies": {**PACKAGE["devDependencies"], "vitest": "^1.0.0"},
            }
        },
        {"web/jest.config.ts": JEST, "web/src/lib/x.ts": "globalThis.window = {}\n"},
        {
            "web/package.json": {**PACKAGE, "devDependencies": {"jest": "^26.6.0"}},
            "web/jest.config.ts": "export default {}\n",
        },
    ],
)
def test_dom_rule_is_skipped_for_ambiguous_environments(tmp_path, variant):
    root = _repo(tmp_path, _web_files(**variant))
    code = "test('a', () => { expect(document.title).toBe('') })\n"
    if "web/src/lib/x.ts" in variant:
        code = "globalThis.window = {}\n" + code
    _run(root, code)


def test_vite_or_extra_tsconfig_fails_open_on_unknown_aliases(tmp_path):
    root = _repo(tmp_path, _web_files(**{"web/vite.config.ts": "export default {}\n"}))
    _run(root, "import { n } from '@/lib/anything-at-all'\n")
    root2 = _repo(tmp_path / "second", _web_files(**{"web/tsconfig.app.json": {"compilerOptions": {}}}))
    _run(root2, "import { n } from '@/lib/anything-at-all'\n")
    with pytest.raises(ValueError, match="alias '@/lib/nope'"):
        _run(_repo(tmp_path / "third", _web_files()), "import { n } from '@/lib/nope'\n")


def test_base_url_and_jest_module_paths_resolve_bare_names(tmp_path):
    root = _repo(
        tmp_path,
        _web_files(**{"web/tsconfig.json": {"compilerOptions": {"baseUrl": "src"}}}),
    )
    _run(root, "import { x } from 'lib/x'\n")
    jest_root = _repo(
        tmp_path / "jest",
        _web_files(
            **{
                "web/tsconfig.json": None,
                "web/jest.config.ts": JEST.replace(
                    "moduleNameMapper", "modulePaths: ['<rootDir>/src'],\n  moduleNameMapper"
                ),
            }
        ),
    )
    (jest_root / "web/tsconfig.json").unlink()
    _run(jest_root, "import { Button } from 'components/Button'\n")
    with pytest.raises(ValueError, match="import 'nothere' in"):
        _run(jest_root, "import { q } from 'nothere/x'\n")


def test_asset_aliases_are_accepted_when_a_mapper_exists(tmp_path):
    _run(_repo(tmp_path, _web_files()), "import styles from '@/styles/app.module.css'\n")
    bare = _repo(tmp_path / "bare", {"web/package.json": PACKAGE})
    with pytest.raises(ValueError, match="neither a repository file nor a configured alias"):
        _run(bare, "import styles from '@/styles/app.css'\n")


def test_module_name_mapper_is_read_for_each_entry(tmp_path):
    jest = (
        "export default {\n  testEnvironment: 'node',\n  moduleNameMapper: {\n"
        "    '\\\\.(css)$': 'identity-obj-proxy',\n    '^~lib/(.*)$': '<rootDir>/src/lib/$1',\n  },\n}\n"
    )
    root = _repo(tmp_path, _web_files(**{"web/jest.config.ts": jest}))
    _run(root, "import { x } from '~lib/x'\n")
    with pytest.raises(ValueError, match="alias '~lib/missing'"):
        _run(root, "import { m } from '~lib/missing'\n")


def test_import_text_inside_strings_and_templates_does_not_count(tmp_path):
    root = _repo(tmp_path, _web_files())
    _run(
        root,
        "const doc = \"import x from 'undeclared-pkg'\"\nconst t = `require('nope-pkg') and import('also-nope')`\n"
        "const m = 'jest.mock(\"x-nope\")'\n",
    )
    with pytest.raises(ValueError, match="import 'undeclared-real' in"):
        _run(root, "const z = require('undeclared-real')\n")


def test_remedies_cover_the_alias_and_missing_file_messages(tmp_path):
    from maf_test_harness.qa.triage import remedy_for

    root = _repo(tmp_path, _web_files())
    messages = []
    for code in (
        "import { m } from '../src/missing'\n",
        "import { n } from '@/lib/nope'\n",
        "import { q } from '~nothing/x'\n",
    ):
        with pytest.raises(ValueError) as caught:
            _run(root, code)
        messages.append(str(caught.value))
    for message in messages:
        assert "tsconfig/jest" in remedy_for(message) or "relative path" in remedy_for(message), message
