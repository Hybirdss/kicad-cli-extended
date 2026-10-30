import hashlib
from pathlib import Path
from unittest.mock import patch
import pytest
from kicad_cli_extended.core import panel
from kicad_cli_extended.utils import kicad_backend as kb
from test_board_gaps import package_session


def test_panel_strict_and_source_output_fail_before_work(package_session, tmp_path):
    source = Path(package_session.project.pcb_path)
    before = source.read_bytes()
    with patch.object(kb, 'run_pcbnew') as worker:
        with pytest.raises(ValueError, match='butted'):
            panel.panelize(package_session, 1, 2, mode='vscore', spacing=2, strict=True, output=str(tmp_path / 'panel.kicad_pcb'))
        with pytest.raises(ValueError, match='separate'):
            panel.panelize(package_session, 1, 2, output=str(source))
    worker.assert_not_called()
    assert source.read_bytes() == before


@pytest.mark.e2e
def test_panel_echoes_coercion_and_preserves_source(package_session, tmp_path):
    board = package_session.project.pcb_path
    kb.run_pcbnew('new_board', {'path': board})
    kb.run_pcbnew('edit', {'path': board, 'out': board, 'actions': [
        {'action': 'outline_rect', 'x': 0, 'y': 0, 'width': 20, 'height': 20}]})
    before = Path(board).read_bytes()
    result = panel.panelize(package_session, 1, 2, mode='vscore', spacing=2, rails='none',
                            fiducials=False, tooling_holes=False, output=str(tmp_path / 'panel.kicad_pcb'))
    assert result['warnings'] and result['spacing'] == 0 and result['requested_spacing'] == 2
    assert result['source_sha256'] == hashlib.sha256(before).hexdigest()
    assert Path(board).read_bytes() == before
