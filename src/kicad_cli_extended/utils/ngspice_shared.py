#!/usr/bin/env python3
"""Run a SPICE netlist through ngspice's shared library (the one KiCad ships) via ctypes.

KiCad does not install an ``ngspice`` executable on Windows or macOS, only the shared
library its simulator loads (``bin/ngspice.dll``, ``KiCad.app/Contents/PlugIns/sim/
libngspice.0.dylib``; Linux: the system ``libngspice.so.0``).  This script drives that
library in batch fashion and writes every simulation plot to an ASCII raw file, like
``ngspice -b -r out.raw``.  It runs as a separate process so a crash in the library cannot
take the CLI down.

Protocol: ``python ngspice_shared.py LIBRARY NETLIST RAW_OUT LOG_OUT``; exit code 0 on success.
Standard library only.
"""

import ctypes
import os
import sys
import tempfile


def main() -> int:
    lib_path, netlist, raw_out, log_out = sys.argv[1:5]
    log = []
    exited = {"status": None}

    cb_char = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
    cb_stat = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
    cb_exit = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_bool, ctypes.c_bool, ctypes.c_int,
                               ctypes.c_void_p)
    cb_data = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
    cb_init = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
    cb_bg = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)

    def on_char(text, ident, user):
        s = (text or b"").decode("utf-8", "replace")
        for prefix in ("stdout ", "stderr "):
            if s.startswith(prefix):
                s = s[len(prefix):]
        log.append(s)
        return 0

    def on_exit(status, unload, quit_, ident, user):
        exited["status"] = status
        return status

    keep = [cb_char(on_char), cb_stat(lambda *a: 0), cb_exit(on_exit), cb_data(lambda *a: 0),
            cb_init(lambda *a: 0), cb_bg(lambda *a: 0)]
    if os.name == "nt":
        # dependent DLLs (libgomp etc.) live next to ngspice.dll in KiCad's bin directory
        os.add_dll_directory(os.path.dirname(os.path.abspath(lib_path)))
    lib = ctypes.CDLL(lib_path)
    lib.ngSpice_Init.argtypes = [ctypes.c_void_p] * 7
    lib.ngSpice_Circ.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
    lib.ngSpice_Command.argtypes = [ctypes.c_char_p]
    lib.ngSpice_AllPlots.restype = ctypes.POINTER(ctypes.c_char_p)
    lib.ngSpice_Init(*[ctypes.cast(k, ctypes.c_void_p) for k in keep], None)

    def cmd(c: str) -> int:
        return lib.ngSpice_Command(c.encode("utf-8"))

    with open(netlist, encoding="utf-8", errors="replace") as f:
        lines = [l.rstrip("\r\n") for l in f]
    arr = (ctypes.c_char_p * (len(lines) + 1))(*[l.encode("utf-8") for l in lines], None)
    rc = lib.ngSpice_Circ(arr)
    ok = rc == 0
    if ok:
        cmd("set filetype=ascii")
        ok = cmd("run") == 0
    plots = []
    p = lib.ngSpice_AllPlots()
    i = 0
    while p and p[i]:
        name = p[i].decode()
        if name != "const":
            plots.append(name)
        i += 1
    parts = []
    for n, name in enumerate(plots):  # ngSpice_AllPlots lists them oldest first, like ngspice -b -r
        fd, part = tempfile.mkstemp(prefix="kce-raw-", suffix=".raw")
        os.close(fd)
        os.unlink(part)
        cwd = os.getcwd()
        try:
            os.chdir(os.path.dirname(part))
            cmd(f"setplot {name}")
            cmd(f"write {os.path.basename(part)} all")
        finally:
            os.chdir(cwd)
        if os.path.exists(part):
            parts.append(part)
    with open(raw_out, "w", encoding="utf-8", newline="\n") as out:
        for part in parts:
            with open(part, encoding="utf-8", errors="replace") as f:
                out.write(f.read())
            os.unlink(part)
    with open(log_out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(log) + "\n")
    return 0 if ok and parts else 1


if __name__ == "__main__":
    sys.exit(main())
