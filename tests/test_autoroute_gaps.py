import importlib.util
import sys
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path


def test_auto_grid_refines_only_until_success(monkeypatch):
    # Load the worker without KiCad to exercise the orchestration and real return contract.
    monkeypatch.setitem(sys.modules, 'pcbnew', SimpleNamespace(FromMM=lambda x: x))
    path = Path(__file__).parents[1] / 'src/kicad_cli_extended/utils/pcbnew_worker.py'
    spec = importlib.util.spec_from_file_location('routing_worker', path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    implementation = worker.a_autoroute_builtin
    calls = []
    def step(board, action):
        calls.append(action['grid'])
        return {'routed': [{'net': str(action['grid'])}], 'failed': [] if action['grid'] == 0.1 else [{'net': 'N'}],
                'unrouted_after': 0 if action['grid'] == 0.1 else 1, 'grid': action['grid']}
    with patch.object(worker, 'a_autoroute_builtin', side_effect=step):
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
