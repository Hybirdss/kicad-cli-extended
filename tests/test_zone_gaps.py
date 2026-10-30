import pytest
from kicad_cli_extended import cli
from kicad_cli_extended.utils import kicad_backend as kb


@pytest.mark.e2e
def test_zone_auto_is_solid_smd_thermal_tht(tmp_path):
    board = str(tmp_path / 'p.kicad_pcb')
    kb.run_pcbnew('new_board', {'path': board})
    result = kb.run_pcbnew('edit', {'path': board, 'out': board, 'actions': [
        {'action': 'add_zone', 'layers': ['F.Cu'], 'points': [[0, 0], [10, 0], [10, 10], [0, 10]], 'fill': False}]})
    assert result['results'][0]['result']['pad_connection_resolved'] == {'smd': 'solid', 'tht': 'thermal'}
    params = cli.zone_add.__click_params__ if hasattr(cli.zone_add, '__click_params__') else cli.zone_add.params
    assert next(p for p in params if p.name == 'pad_connection').default == 'auto'
    from kicad_cli_extended.core import sexpr
    assert sexpr.load(board).find('zone').find('connect_pads').atoms()[0] == 'thru_hole_only'
