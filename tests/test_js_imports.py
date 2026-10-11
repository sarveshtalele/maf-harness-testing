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
