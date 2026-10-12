#!/usr/bin/env python3
"""Run Python CI regression scripts with an explicit socket guard installed first.

Usage:
    python tools/ci_offline_guard/runner.py tools/test_thing.py [args]
    python tools/ci_offline_guard/runner.py -m pytest -q tools/test_thing.py
    python tools/ci_offline_guard/runner.py -m compileall -q path.py
    python tools/ci_offline_guard/runner.py - < script.py

This guards the current Python process only, not arbitrary native subprocesses.
"""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys

from net_guard import install


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("missing module/script argument")
    if os.environ.get("KLD_PR_OFFLINE") != "1":
        raise RuntimeError("KLD_PR_OFFLINE=1 required; refuse unguarded execution")
    install()
    # The working-tree root is required for tests under tools/ to import post_kld.
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    command, *args = sys.argv[1:]
    if command == "-m":
        if not args:
            raise SystemExit("missing -m module")
        module, *margs = args
        if module == "pytest":
            os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            import pytest
            raise SystemExit(pytest.main(margs))
        sys.argv = [module] + margs
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    elif command == "-":
        sys.argv = ["-"] + args
        source = sys.stdin.read()
        scope = {"__name__": "__main__", "__file__": "<stdin>"}
        exec(compile(source, "<stdin>", "exec"), scope)
    elif command == "-c":
        if not args:
            raise SystemExit("missing -c code")
        source, *extra = args
        sys.argv = ["-c"] + extra
        scope = {"__name__": "__main__", "__file__": "<command>"}
        exec(compile(source, "<command>", "exec"), scope)
    else:
        path = Path(command)
        if not path.is_file():
            raise RuntimeError("script is missing: " + command)
        sys.argv = [command] + args
        sys.path.insert(0, str(path.resolve().parent))
        runpy.run_path(str(path), run_name="__main__")


if __name__ == "__main__":
    main()
