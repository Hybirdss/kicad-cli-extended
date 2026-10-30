"""Extract tool evidence from local agent session logs without copying reasoning or credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def capture(sessions, prefix):
    evidence = []
    for path in sorted(sessions.glob("*.jsonl")):
        try:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        except (OSError, ValueError):
            continue
        meta = rows[0].get("payload", {}) if rows else {}
        source = meta.get("source", {})
        if not isinstance(source, dict):
            continue
        name = source.get("subagent", {}).get("thread_spawn", {}).get("agent_path", "")
        if not name.startswith(prefix):
            continue
        calls = {}
        for row in rows:
            payload = row.get("payload", {})
            if payload.get("type") == "custom_tool_call":
                calls[payload["call_id"]] = payload
            elif payload.get("type") == "custom_tool_call_output" and payload.get("call_id") in calls:
                call = calls[payload["call_id"]]
                script = call.get("input", "")
                if not any(marker in script for marker in ("cat ", "sed ", "rg ", "info describe", "--help")):
                    continue
                output = payload.get("output", "")
                if not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False)
                evidence.append({"agent": name, "session_id": meta["id"], "session_log": str(path),
                    "timestamp": row.get("timestamp"), "command": script,
                    "output_bytes": len(output.encode("utf-8")),
                    "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
                    "output_head": output[:1200], "output_tail": output[-500:]})
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sessions", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--prefix", default="/root/cold_acceptance/r")
    args = parser.parse_args()
    evidence = capture(args.sessions, args.prefix)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"actual_tool_output_records": len(evidence),
                      "agents": sorted({row["agent"] for row in evidence})}))
    return int(not evidence)


if __name__ == "__main__":
    raise SystemExit(main())
