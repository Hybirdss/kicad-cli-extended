"""Run the suite against a throwaway KiCad config, so it never edits the user's KiCad settings."""
import os
import shutil
import subprocess
import sys
import tempfile

_config_home = None


def pytest_configure(config):
    global _config_home
    if os.environ.get("KICAD_CONFIG_HOME"):
        return
    _config_home = tempfile.mkdtemp(prefix="kce-kicad-config-")
    os.environ["KICAD_CONFIG_HOME"] = _config_home
    # KiCad's default library tables, which a configured KiCad (and CI) has
    r = subprocess.run([sys.executable, "-m", "kicad_cli_extended", "--json", "lib", "init-global-tables"],
                       capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    if r.returncode not in (0, 7):  # 7, the environment category: KiCad is not installed
        raise RuntimeError(f"kce lib init-global-tables failed:\n{r.stderr}")


def pytest_unconfigure(config):
    if _config_home:
        shutil.rmtree(_config_home, ignore_errors=True)
