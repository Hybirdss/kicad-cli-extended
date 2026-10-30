"""Compact recorded CLI executions while retaining raw-output hashes and parsed result evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--plan", required=True)
    args = parser.parse_args()
    data = args.source.read_bytes()
    calls = []
    for line in data.decode("utf-8").splitlines():
        call = json.loads(line)
        output = call.pop("stdout")
        call["stdout_sha256"] = hashlib.sha256(output.encode("utf-8")).hexdigest()
        call["stdout_bytes"] = len(output.encode("utf-8"))
        try:
            result = json.loads(output)
        except ValueError:
            result = [json.loads(row) for row in output.splitlines()] if output.strip() else None
        if isinstance(result, list) and result and isinstance(result[0], dict) and "command" in result[0]:
            result = {"command_count": len(result), "command_paths": [row["command"] for row in result]}
        call["result"] = result
        calls.append(call)
    result = {"recipe": args.recipe, "source_plan": args.plan,
              "raw_log": str(args.source), "raw_log_sha256": hashlib.sha256(data).hexdigest(),
              "calls": calls}
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"recipe": args.recipe, "calls": len(calls),
                      "nonzero_exits": [call["exit"] for call in calls if call["exit"]],
                      "raw_log_sha256": result["raw_log_sha256"]}))


if __name__ == "__main__":
    main()
