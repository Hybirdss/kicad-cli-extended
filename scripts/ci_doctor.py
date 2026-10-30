"""CI gate: `kce --json info doctor` must print one JSON object with "ok": true.

`info doctor` exits 0 even when KiCad is broken, so the exit code alone gates nothing.
"""
import json
import subprocess
import sys

r = subprocess.run(["kce", "--json", "info", "doctor"], capture_output=True, text=True, encoding="utf-8")
sys.stderr.write(r.stderr)
try:
    doctor = json.loads(r.stdout)
except ValueError:
    print(r.stdout)
    sys.exit("kce info doctor did not print JSON")
print(json.dumps(doctor, indent=2))
sys.exit(0 if doctor.get("ok") else 1)
