"""Native KiCad regressions for proposed router geometry and connectivity."""
import json

import pytest

from kicad_cli_extended.utils import kicad_backend as kb

pytestmark = pytest.mark.e2e


def make_board(tmp_path, setup):
    path = str(tmp_path / 'p.kicad_pcb')
    kb.run_pcbnew('new_board', {'path': path})
    kb.run_pcbnew('edit', {'path': path, 'out': path, 'actions': [
        {'action': 'script', 'code': setup},
        {'action': 'outline_rect', 'x': 0, 'y': 0, 'width': 12, 'height': 12}]})
    return path


PADS = '''
for name, coords in placements:
    net = pcbnew.NETINFO_ITEM(board, name)
    board.Add(net)
    for i, (x, y, layer) in enumerate(coords):
        fp = pcbnew.FOOTPRINT(board)
        fp.SetReference(name + str(i + 1))
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        board.Add(fp)
        fp.SetPosition(vec(x, y))
        pad = pcbnew.PAD(fp)
        fp.Add(pad)
        pad.SetNumber('1')
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        pad.SetSize(vec(size, size))
        ls = pcbnew.LSET()
        ls.AddLayer(layer)
        pad.SetLayerSet(ls)
        pad.SetPosition(vec(x, y))
        pad.SetNet(net)
'''


def route(path, grid, layers=None):
    return kb.run_pcbnew('edit', {'path': path, 'out': path, 'actions': [
        {'action': 'autoroute_builtin', 'grid': grid, 'width': .2, 'clearance': .2,
         'layers': layers or ['F.Cu', 'B.Cu']}]})['results'][-1]['result']


def test_new_via_annulus_blocks_later_net_on_both_layers(tmp_path):
    path = make_board(tmp_path, '''
size = .2
placements = [('A', [(5, 8, pcbnew.F_Cu), (5, 9, pcbnew.B_Cu)]),
              ('B', [(4, 8.6, pcbnew.B_Cu), (6, 8.6, pcbnew.B_Cu)])]
''' + PADS)
    result = route(path, .1)
    assert len(result['routed']) == 2 and not result['failed']
    assert kb.run_pcbnew('connectivity', {'path': path})['fully_routed']
    report = tmp_path / 'drc.json'
    kb.run_kicad_cli(['pcb', 'drc', '--format', 'json', '-o', str(report), path])
    violations = json.loads(report.read_text())['violations']
    assert not [v for v in violations if v['severity'] == 'error'], violations


def test_coincident_grid_cells_are_not_reported_as_a_route(tmp_path):
    path = make_board(tmp_path, '''
size = .02
placements = [('N', [(5, 5, pcbnew.F_Cu), (5.06, 5, pcbnew.F_Cu)])]
''' + PADS)
    assert not kb.run_pcbnew('connectivity', {'path': path})['fully_routed']
    result = route(path, 1, ['F.Cu'])
    assert not result['routed']
    assert result['failed'] and result['unrouted_after'] > 0
    assert not kb.run_pcbnew('connectivity', {'path': path})['fully_routed']
    refined = route(path, 'auto', ['F.Cu'])
    assert len(refined['grid_tried']) > 1
    assert refined['routed'] and not refined['failed']
    assert kb.run_pcbnew('connectivity', {'path': path})['fully_routed']
