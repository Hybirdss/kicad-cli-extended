"""Transparent foreground CLI recorder, including interactive JSONL serve sessions."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading


def main():
    command = [os.environ["KCE_AUDIT_CLI"], *sys.argv[1:]]
    process = subprocess.Popen(command, stdin=sys.stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8")
    stdout, stderr = [], []

    def forward(stream, destination, captured):
        for line in stream:
            captured.append(line)
            destination.write(line)
            destination.flush()
        stream.close()

    readers = [threading.Thread(target=forward, args=(process.stdout, sys.stdout, stdout)),
               threading.Thread(target=forward, args=(process.stderr, sys.stderr, stderr))]
    for reader in readers:
        reader.start()
    try:
        result = process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()
        for reader in readers:
            reader.join()
    if os.environ.get("KCE_AUDIT_LOG"):
        with open(os.environ["KCE_AUDIT_LOG"], "a", encoding="utf-8") as log:
            log.write(json.dumps({"args": sys.argv[1:], "exit": result,
                "stdout": "".join(stdout), "stderr": "".join(stderr)}, ensure_ascii=False) + "\n")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
