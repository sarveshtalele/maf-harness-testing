#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

function parseOptions(args) {
  const options = { output: 'output/junit-tests' };
  for (let index = 0; index < args.length; index += 1) {
    const key = args[index];
    if (!['--input', '--output', '--package'].includes(key)) continue;
    const value = args[index + 1];
    if (!value || value.startsWith('--')) throw new Error(`Missing value for ${key}`);
    options[key.slice(2)] = value;
    index += 1;
  }
  if (!options.input) throw new Error('Missing required --input <file> option');
  return options;
}

let options;
try { options = parseOptions(process.argv.slice(2)); }
catch (error) { console.error(JSON.stringify({ error: { code: 'INVALID_OPTIONS', message: error.message } })); process.exit(1); }

function fail(code, message, details = {}) {
  console.error(JSON.stringify({ error: { code, message, ...details } }));
  process.exitCode = 1;
}

function sanitizeJavaIdentifier(value) {
  const identifier = String(value || '').replace(/[^A-Za-z0-9_]/g, '_');
  const safeIdentifier = /^[A-Za-z_]/.test(identifier) ? identifier : `_${identifier}`;
  const keywords = new Set(['abstract', 'assert', 'boolean', 'break', 'byte', 'case', 'catch', 'char', 'class', 'const', 'continue', 'default', 'do', 'double', 'else', 'enum', 'extends', 'final', 'finally', 'float', 'for', 'goto', 'if', 'implements', 'import', 'instanceof', 'int', 'interface', 'long', 'native', 'new', 'package', 'private', 'protected', 'public', 'return', 'short', 'static', 'strictfp', 'super', 'switch', 'synchronized', 'this', 'throw', 'throws', 'transient', 'try', 'void', 'volatile', 'while']);
  return keywords.has(safeIdentifier) ? `${safeIdentifier}_` : safeIdentifier;
}

function toJavaClassName(id) { return `Test${sanitizeJavaIdentifier(id)}`; }
function javaString(value) { return JSON.stringify(String(value)); }
function javaComment(value) { return String(value).replace(/[\r\n]+/g, ' ').replace(/\*\//g, '* /'); }

function normalizeCases(data) {
  if (Array.isArray(data.test_cases)) return data.test_cases;
  const types = {
    positive_tests: 'positive', negative_tests: 'negative', boundary_tests: 'boundary',
    validation_tests: 'validation', contract_tests: 'contract'
  };
  return Object.entries(types).flatMap(([key, type]) => (Array.isArray(data[key]) ? data[key] : [])
    .map(testCase => ({ ...testCase, type: testCase.type || type })));
}

function fixtureMembers(fixture) {
  const data = fixture && fixture.data && typeof fixture.data === 'object' ? fixture.data : {};
  const fields = [];
  const setup = [];
  const sources = [];
  const nested = [];
  for (const [key, value] of Object.entries(data)) {
    const name = sanitizeJavaIdentifier(key);
    if (Array.isArray(value)) {
      sources.push(`    static java.util.stream.Stream<Object> ${name}Values() {\n        return java.util.stream.Stream.of(${value.map(javaString).join(', ')});\n    }`);
    } else if (value && typeof value === 'object') {
      nested.push(`    static class ${name}Fixture {\n        // TODO: Map nested fixture ${javaString(key)} with a builder or explicit fields.\n    }`);
    } else {
      const type = typeof value === 'number' ? 'double' : typeof value === 'boolean' ? 'boolean' : 'String';
      fields.push(`    private ${type} ${name};`);
      setup.push(`        ${name} = ${type === 'String' ? javaString(value) : value};`);
    }
  }
  return { fields, setup, sources, nested };
}

function methodFor(testCase, fixture) {
  const title = testCase.title || testCase.name || testCase.id;
  const expected = testCase.expected_result ? `        // TODO: Expected result: ${javaComment(testCase.expected_result)}\n` : '';
  const steps = Array.isArray(testCase.steps) && testCase.steps.length ? testCase.steps : ['TODO: Add test steps'];
  const stepComments = steps.map(step => `        // ${javaComment(step)}`).join('\n');
  const baseName = `test_${sanitizeJavaIdentifier(testCase.id)}_${sanitizeJavaIdentifier(title).slice(0, 40)}`;
  if (testCase.type === 'negative') {
    return `    @Test\n    @Tag("negative")\n    @DisplayName(${javaString(title)})\n    void ${baseName}() {\n${stepComments}\n\n        // TODO: Replace RuntimeException with the documented exception type.\n        assertThrows(RuntimeException.class, () -> {\n            // TODO: Invoke the component under test.\n        });\n${expected}    }`;
  }
  if (testCase.type === 'boundary') {
    const source = fixture && fixture.data && Object.entries(fixture.data).find(([, value]) => Array.isArray(value));
    const sourceName = source ? `${sanitizeJavaIdentifier(source[0])}Values` : 'boundaryValues';
    const helper = source ? '' : `\n    static java.util.stream.Stream<Object> boundaryValues() {\n        return java.util.stream.Stream.empty(); // TODO: Provide boundary values.\n    }\n`;
    return `${helper}    @ParameterizedTest\n    @MethodSource("${sourceName}")\n    @DisplayName(${javaString(title)})\n    void ${baseName}(Object boundaryValue) {\n${stepComments}\n        // TODO: Use boundaryValue when invoking the component under test.\n${expected}    }`;
  }
  return `    @Test\n    @DisplayName(${javaString(title)})\n    void ${baseName}() {\n${stepComments}\n\n        // TODO: Invoke the component under test.\n${expected}        // TODO: Add a specific assertion supported by the expected result.\n    }`;
}

function generateJavaClass(testCase, fixture, packageName) {
  const members = fixtureMembers(fixture);
  const traceability = Array.isArray(testCase.traceability_links) ? javaComment(testCase.traceability_links.map(link => link.id || link).join(', ')) : 'N/A';
  const packageLine = packageName ? `package ${packageName};\n\n` : '';
  return `${packageLine}import org.junit.jupiter.api.BeforeEach;\nimport org.junit.jupiter.api.DisplayName;\nimport org.junit.jupiter.api.Tag;\nimport org.junit.jupiter.api.Test;\nimport org.junit.jupiter.params.ParameterizedTest;\nimport org.junit.jupiter.params.provider.MethodSource;\nimport static org.junit.jupiter.api.Assertions.assertThrows;\n\n/**\n * Auto-generated JUnit test class\n * Generated from test case: ${testCase.id}\n * Traceability: ${traceability}\n */\n@DisplayName(${javaString(testCase.title || testCase.name || testCase.id)})\npublic class ${toJavaClassName(testCase.id)} {\n\n    private Object componentUnderTest;\n${members.fields.length ? `\n${members.fields.join('\n')}\n` : ''}\n    @BeforeEach\n    void setUp() {\n        // TODO: Initialize componentUnderTest with the real application type.\n${members.setup.length ? `${members.setup.join('\n')}\n` : ''}    }\n\n${methodFor(testCase, fixture)}\n${members.sources.length ? `\n${members.sources.join('\n\n')}\n` : ''}${members.nested.length ? `\n${members.nested.join('\n\n')}\n` : ''}}\n`;
}

function main() {
  if (!fs.existsSync(options.input)) return fail('MISSING_INPUT', `Input file not found: ${options.input}`, { path: options.input });
  let data;
  try { data = JSON.parse(fs.readFileSync(options.input, 'utf8')); }
  catch (error) { return fail('INVALID_JSON', error.message, { path: options.input }); }
  const testCases = normalizeCases(data);
  const fixtures = new Map((Array.isArray(data.fixtures) ? data.fixtures : []).filter(item => item && item.id).map(item => [item.id, item]));
  fs.mkdirSync(options.output, { recursive: true });
  const manifest = [];
  for (const testCase of testCases) {
    if (!testCase || !testCase.id) { console.warn(JSON.stringify({ warning: { code: 'MISSING_REQUIRED_FIELD', message: 'Skipped test case without id' } })); continue; }
    const fixture = testCase.fixture_id ? fixtures.get(testCase.fixture_id) : undefined;
    if (testCase.fixture_id && !fixture) console.warn(JSON.stringify({ warning: { code: 'MISSING_FIXTURE', id: testCase.id, fixture_id: testCase.fixture_id } }));
    const className = toJavaClassName(testCase.id);
    const file = `${className}.java`;
    const filePath = path.join(options.output, file);
    fs.writeFileSync(filePath, generateJavaClass(testCase, fixture, options.package), 'utf8');
    manifest.push({ id: testCase.id, class: className, file, path: filePath, type: testCase.type || 'positive' });
  }
  fs.writeFileSync(path.join(options.output, 'test_classes.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  if (!testCases.length) console.warn(JSON.stringify({ warning: { code: 'EMPTY_TEST_CASES', message: 'Wrote an empty manifest' } }));
  console.log(JSON.stringify({ generated: manifest.length, manifest: path.join(options.output, 'test_classes.json') }));
}

main();
