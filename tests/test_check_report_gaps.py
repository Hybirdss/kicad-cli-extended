"""Rule-check failures must never be replaced by a previous successful report."""
import json
import os
import subprocess
import sys

import pytest

from kicad_cli_extended.core import checks
from kicad_cli_extended.utils import kicad_backend as kb


def invoke(*args):
    return subprocess.run([sys.executable, '-m', 'kicad_cli_extended', '--json',
                           *map(str, args)], capture_output=True, text=True, timeout=120)


@pytest.mark.e2e
@pytest.mark.parametrize('kind', ['erc', 'drc'])
@pytest.mark.parametrize('existing_output', [False, True])
def test_failed_real_engine_never_reuses_clean_report(tmp_path, kind, existing_output):
    project = tmp_path / 'board'
    made = invoke('project', 'new', project)
    assert made.returncode == 0, made.stderr
    if kind == 'drc':
        outline = invoke('-p', project, 'pcb', 'outline', 'rect', '--at', '0,0', '--size', '20,20')
        assert outline.returncode == 0, outline.stderr
    report = tmp_path / 'report.json'
    command = ['-p', project, 'check', kind, '--fail-on-error']
    if existing_output:
        command += ['-o', report]
    good = invoke(*command)
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)['clean'] is True
    previous = report.read_bytes() if existing_output else None
    extension = 'kicad_sch' if kind == 'erc' else 'kicad_pcb'
    damaged = project / ('board.' + extension)
    damaged.write_text(f'({extension} (version 20260101) (layers BROKEN))', encoding='utf-8')
    bad = invoke(*command)
    assert bad.returncode != 0, bad.stdout
    assert '"clean":true' not in bad.stdout.replace(' ', '')
    error = json.loads(bad.stderr)
    assert error['type'] != 'JSONDecodeError'
    assert 'failed' in error['error'].lower() or 'load' in error['error'].lower()
    assert damaged.read_text().endswith('(layers BROKEN))')
    if existing_output:
        assert report.read_bytes() == previous
    assert not list(tmp_path.glob('.erc-*.json'))
    assert not list(tmp_path.glob('.drc-*.json'))


@pytest.mark.parametrize('content', ['', '{}', '[]', '{invalid'])
def test_success_exit_without_valid_report_is_not_clean(tmp_path, content):
    path = tmp_path / 'bad.json'
    path.write_text(content)
    with pytest.raises(kb.KicadError, match='no valid fresh report'):
        checks._read_check_report(str(path), {'returncode': 0, 'stdout': 'engine output'}, 'drc', None)


def test_explicit_violation_exit_still_loads_current_report(tmp_path):
    path = tmp_path / 'violations.json'
    path.write_text('{"violations": [{"severity": "error"}]}')
    result = {'returncode': 5, 'stdout': 'violations found'}
    assert checks._read_check_report(str(path), result, 'drc', ['--exit-code-violations'])['violations']
    with pytest.raises(kb.KicadError, match='exited with code 5'):
        checks._read_check_report(str(path), result, 'drc', None)
