import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from kicad_cli_extended import cli
from kicad_cli_extended.core import pcb
from kicad_cli_extended.core.project import KicadProject
from kicad_cli_extended.core.session import Session
from kicad_cli_extended.utils import kicad_backend as kb
from test_sync_gaps import sync_board

pytestmark = pytest.mark.e2e


def test_eco_scopes_lock_near_legalize_and_routing(sync_board, monkeypatch):
    board, lib = sync_board
    Path(board).with_suffix('.kicad_pro').write_text('{}')
    kb.run_pcbnew('edit', {'path': board, 'out': board, 'actions': [
        {'action': 'outline_rect', 'x': 0, 'y': 0, 'width': 40, 'height': 40},
        {'action': 'add_fp', 'lib_path': lib, 'name': 'Old', 'ref': 'C10', 'x': 10, 'y': 10, 'pad_nets': {'1': 'VCC'}}]})
    sess = Session()
    sess.set_project(KicadProject(str(Path(board).with_suffix('.kicad_pro'))))
    monkeypatch.setattr(cli, '_session', sess)
    result = CliRunner().invoke(cli.cli, ['--json', 'pcb', 'lock', '--all', '--except', 'C10'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)['locked'] == ['C2']
    before = pcb.read(sess, 'footprint', ref='C2')
    legal = pcb.legalize(sess, refs=['C10'], max_distance=5)
    assert {m['ref'] for m in legal['moved']} == {'C10'}
    near = pcb.place_near(sess, 'C10', 'C2', net='VCC', max_distance=6, rotation='auto')
    pad = next(p for p in before['pads'] if p['number'] == '1')
    assert near['target'] == 'C2:1' and near['target_at'] == [pad['x'], pad['y']]
    assert pcb.read(sess, 'footprint', ref='C2') == before
    route = pcb.autoroute(sess, refs=['C10'], area=[0, 0, 40, 40])
    assert route['routed'] and all(c['from'].startswith('C10:') or c['to'].startswith('C10:') for c in route['routed'] + route['failed'])
    assert pcb.read(sess, 'footprint', ref='C2') == before
