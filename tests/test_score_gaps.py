import json
from pathlib import Path

import jsonschema
import pytest
from click.testing import CliRunner
from kicad_cli_extended import cli
from kicad_cli_extended.core.project import KicadProject
from kicad_cli_extended.core.session import Session
from kicad_cli_extended.utils import kicad_backend as kb

pytestmark = pytest.mark.e2e


def test_score_schema_determinism_and_missing_outline_gate(tmp_path, monkeypatch):
    board = str(tmp_path / 'p.kicad_pcb')
    kb.run_pcbnew('new_board', {'path': board})
    missing = kb.run_pcbnew('score', {'path': board})
    assert missing['outline']['present'] is False and missing['error']
    pro = tmp_path / 'p.kicad_pro'
    pro.write_text('{}')
    sess = Session()
    sess.set_project(KicadProject(str(pro)))
    monkeypatch.setattr(cli, '_session', sess)
    result = CliRunner().invoke(cli.cli, ['--json', 'pcb', 'score'])
    assert result.exit_code == 3 and json.loads(result.stdout)['error']
    kb.run_pcbnew('edit', {'path': board, 'out': board, 'actions': [
        {'action': 'outline_rect', 'x': 0, 'y': 0, 'width': 20, 'height': 20}]})
    first = kb.run_pcbnew('score', {'path': board})
    second = kb.run_pcbnew('score', {'path': board})
    assert json.dumps(first, allow_nan=False) == json.dumps(second, allow_nan=False)
    assert first['outline']['closed'] and first['error'] is None
    schema = json.loads((Path(__file__).parents[1] / 'schemas/pcb-score.schema.json').read_text())
    jsonschema.validate(first, schema)
    jsonschema.validate(missing, schema)
