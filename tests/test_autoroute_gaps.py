import importlib.util
import json
import os
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

import route_boards
from test_e2e import rj, run, tmp_dir  # noqa: F401


def test_auto_grid_refines_only_until_success(monkeypatch):
    # Load the worker without KiCad to exercise the orchestration and real return contract.
    monkeypatch.setitem(sys.modules, 'pcbnew', SimpleNamespace(FromMM=lambda x: x))
    path = Path(__file__).parents[1] / 'src/kicad_cli_extended/utils/pcbnew_worker.py'
    spec = importlib.util.spec_from_file_location('routing_worker', path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    implementation = worker._route
    calls = []
    def step(board, action):
        calls.append(action['grid'])
        return {'routed': [{'net': str(action['grid'])}], 'failed': [] if action['grid'] == 0.1 else [{'net': 'N'}],
                'unrouted_after': 0 if action['grid'] == 0.1 else 1, 'grid': action['grid']}
    with patch.object(worker, '_route', side_effect=step):
        result = implementation(object(), {'grid': 'auto'})
    assert calls == result['grid_tried'] == [0.25, 0.15, 0.1]
    assert result['failed'] == [] and len(result['routed']) == 3


def test_auto_grid_routes_real_board(tmp_path):
    from kicad_cli_extended.utils import kicad_backend as kb
    path = str(tmp_path / 'p.kicad_pcb')
    kb.run_pcbnew('new_board', {'path': path})
    setup = '''
net = pcbnew.NETINFO_ITEM(board, 'SIGNAL')
board.Add(net)
for i, x in enumerate((5, 15)):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetReference('J' + str(i + 1))
    board.Add(fp)
    fp.SetPosition(vec(x, 10))
    pad = pcbnew.PAD(fp)
    fp.Add(pad)
    pad.SetNumber('1')
    pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
    pad.SetSize(vec(1.6, 1.6))
    pad.SetDrillSize(vec(0.8, 0.8))
    pad.SetPosition(vec(x, 10))
    pad.SetNet(net)
'''
    result = kb.run_pcbnew('edit', {'path': path, 'out': path, 'actions': [
        {'action': 'script', 'code': setup},
        {'action': 'outline_rect', 'x': 0, 'y': 0, 'width': 20, 'height': 20},
        {'action': 'autoroute_builtin', 'grid': 'auto'}]})['results'][-1]['result']
    assert result['grid_tried'] == [0.25]
    assert result['routed'] and result['failed'] == [] and result['unrouted_after'] == 0
    assert kb.run_pcbnew('connectivity', {'path': path})['fully_routed']


def test_autoroute_does_not_blame_a_route_for_an_old_clearance_error(tmp_dir):
    # The GND track already violates clearance; routing SIG far from it adds nothing. KiCad reports
    # one error per track by default, naming pad 2 or pad 6 by item order, and the pads get new UUIDs
    # on every load, so the old finding looked new after the save and the route was refused.
    p = os.path.join(tmp_dir, 'legacy')
    run(['project', 'new', p, '--name', 'legacy'])
    os.remove(os.path.join(p, 'legacy.kicad_sch'))
    with open(os.path.join(p, 'legacy.kicad_pcb'), 'w', encoding='utf-8') as f:
        f.write(route_boards.kicad5(15, "track", pre_existing_violation=True)["legacy"])
    before = json.loads(run(['--json', '--project', p, 'check', 'drc']).stdout)
    assert before['by_type'].get('clearance'), before['by_type']
    r = run(['--json', '--project', p, 'pcb', 'autoroute', '--net', 'SIG', '--layers', 'F.Cu', '--grid', '0.25'],
            check=False)
    assert r.returncode == 0, r.stderr[-2000:]
    assert [c['net'] for c in json.loads(r.stdout)['routed']] == ['SIG']


def test_placement_does_not_blame_a_move_for_an_old_clearance_error(tmp_dir):
    # The same board: moving S1 next to S2 adds no error, but the old one names pads and a track that
    # KiCad 5 saved without UUIDs, so each load gave them new ones and the old error looked new.
    p = os.path.join(tmp_dir, 'legacy')
    run(['project', 'new', p, '--name', 'legacy'])
    os.remove(os.path.join(p, 'legacy.kicad_sch'))
    with open(os.path.join(p, 'legacy.kicad_pcb'), 'w', encoding='utf-8') as f:
        f.write(route_boards.kicad5(15, "track", pre_existing_violation=True)["legacy"])
    r = run(['--json', '--project', p, 'pcb', 'place', 'near', 'S1', '--to', 'S2'], check=False)
    assert r.returncode == 0, r.stderr[-2000:]


def test_auto_grid_stops_before_a_grid_too_large_for_the_board(tmp_path):
    from kicad_cli_extended.utils import kicad_backend as kb
    path = str(tmp_path / 'p.kicad_pcb')
    kb.run_pcbnew('new_board', {'path': path})
    setup = '''
nets = {name: pcbnew.NETINFO_ITEM(board, name) for name in ('SIGNAL', 'ROUTED')}
for net in nets.values():
    board.Add(net)
for i, (name, x, y) in enumerate((('SIGNAL', 10, 10), ('SIGNAL', 90, 70), ('ROUTED', 40, 40), ('ROUTED', 50, 40))):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetReference('J' + str(i + 1))
    board.Add(fp)
    fp.SetPosition(vec(x, y))
    pad = pcbnew.PAD(fp)
    fp.Add(pad)
    pad.SetNumber('1')
    pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
    pad.SetSize(vec(1.6, 1.6))
    pad.SetDrillSize(vec(0.8, 0.8))
    pad.SetPosition(vec(x, y))
    pad.SetNet(nets[name])
'''
    # keepouts around both SIGNAL pads make that connection unroutable at every grid
    keepouts = [{'action': 'add_zone', 'keepout': True, 'layers': ['F.Cu', 'B.Cu'], 'rect': [x - 2, y - 2, 4, 4]}
                for x, y in ((10, 10), (90, 70))]
    result = kb.run_pcbnew('edit', {'path': path, 'out': path, 'actions': [
        {'action': 'script', 'code': setup},
        {'action': 'outline_rect', 'x': 0, 'y': 0, 'width': 100, 'height': 80},
        *keepouts,
        {'action': 'autoroute_builtin', 'grid': 'auto'}]})['results'][-1]['result']
    # a 0.05 mm grid over 100 x 80 mm on two layers is 6.4 million cells, over the router's limit
    assert result['grid_tried'] == [0.25, 0.15, 0.1]
    assert [c['net'] for c in result['routed']] == ['ROUTED']
    assert [c['net'] for c in result['failed']] == ['SIGNAL']
    assert '0.05 mm' in result['warning']
    assert [n['net'] for n in kb.run_pcbnew('connectivity', {'path': path})['nets']] == ['SIGNAL']


def test_autoroute_leaves_the_fill_of_a_stale_pour_as_it_was(tmp_dir):
    # A GND pour that keeps its islands, made stale by a track added after the fill. Refilling the
    # routed board itself to judge the route once marked that fill as an island in the saved file,
    # and the ECO gate then reported the zone as changed.
    p = route_boards.build({
        "pads": [['GP1', '1', 'GND', 30, 15], ['GP2', '1', 'GND', 30, 25], ['S1', '1', 'SIG', 47, 2],
                 ['S2', '1', 'SIG', 47, 38]],
        "actions": [{'action': 'add_zone', 'net': 'GND', 'layers': ['F.Cu'], 'rect': [4, 4, 52, 32],
                     'island_removal': 'never', 'fill': False}],
        "after_fill": [['pcb', 'track', '8,1;8,39', '--layer', 'F.Cu', '--width', '0.25', '--net', 'SIG3']]},
        tmp_dir, lambda args, check: run(args, check=check))
    P = ['--project', p]
    checkpoint = os.path.join(tmp_dir, 'checkpoint')
    shutil.copytree(p, checkpoint)
    assert rj(P + ['pcb', 'autoroute', '--net', 'SIG', '--layers', 'F.Cu', '--grid', '0.25'])['lost_connections'] == []
    run(P + ['project', 'compare', checkpoint, '--allow', 'net:SIG', '--fail-on-unexpected'])
