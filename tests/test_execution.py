import json
import sys

import pytest

from maf_test_harness import execution


def test_command_result_records_failure_and_sanitizes_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-leak')
    report = execution.run_command([sys.executable, '-c',
        'import os,sys; print(os.getenv("OPENAI_API_KEY", "absent")); sys.exit(3)'], tmp_path, tmp_path/'result')
    assert report['status'] == 'failed'
    assert report['exit_code'] == 3
    assert (tmp_path/'result/stdout.log').read_text().strip() == 'absent'
    assert json.loads((tmp_path/'result/result.json').read_text()) == report


def test_timeout_is_not_success(tmp_path):
    report = execution.run_command([sys.executable, '-c', 'import time; time.sleep(5)'],
                                   tmp_path, tmp_path/'result', timeout=0.1)
    assert report['status'] == 'timeout'


def test_junit_zero_or_skipped_tests_never_pass(tmp_path):
    xml = tmp_path/'report.xml'
    xml.write_text('<testsuites><testsuite tests="1"><testcase name="x"><skipped/></testcase></testsuite></testsuites>')
    summary = execution.read_junit([xml])
    assert summary == {'tests': 1, 'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 1}
    report = execution.run_command([sys.executable, '-c', 'print("no tests")'], tmp_path,
                                   tmp_path/'result', junit_glob='fresh/*.xml')
    assert report['status'] == 'no_test_evidence'


def test_export_prevents_overwrite_and_requires_complete_case_coverage(tmp_path):
    repo = tmp_path/'repo'
    repo.mkdir()
    (repo/'test_existing.py').write_text('original')
    payload = {'files': [{'path': 'test_existing.py', 'content': '# CASE_ID: TC-1\ndef test_value():\n    assert value() == 3\n', 'case_ids': ['TC-1']}]}
    with pytest.raises(ValueError, match='overwrite'):
        execution.export_tests(repo, payload, {'TC-1'}, tmp_path/'export')
    assert (repo/'test_existing.py').read_text() == 'original'
    payload['files'][0]['path'] = 'tests/test_value.py'
    with pytest.raises(ValueError, match='uncovered'):
        execution.export_tests(repo, payload, {'TC-1', 'TC-2'}, tmp_path/'export2')


def test_exported_tests_execute_against_real_application(tmp_path):
    repo = tmp_path/'repo'
    repo.mkdir()
    (repo/'service.py').write_text('def add(a, b):\n    return a + b\n')
    payload = {'files': [{'path': 'test_add.py', 'case_ids': ['TC-1'],
        'content': '# CASE_ID: TC-1\nfrom service import add\ndef test_add():\n    assert add(2, 3) == 5\n'}]}
    work = execution.export_tests(repo, payload, {'TC-1'}, tmp_path/'workspace')
    report = execution.run_command([sys.executable, '-m', 'pytest', 'test_add.py', '--junitxml=result.xml'],
                                   work, tmp_path/'logs', junit_glob='result.xml')
    assert report['status'] == 'passed'
    assert report['test_results']['passed'] == 1
    assert not (repo/'test_add.py').exists()
