"""Explicit, opt-in socket-denial for a single KLD CI Python test process.

This module must be installed through ``runner.py`` BEFORE importing any
repository/application code; never use it for dependency installation.
Caught exceptions are still recorded in a shared audit ledger.
"""
from __future__ import annotations

import os
import socket
import subprocess


def install() -> None:
    path = os.environ.get("KLD_PR_OFFLINE_AUDIT")
    if not path or not os.path.isabs(path):
        raise RuntimeError("KLD PR offline runner requires absolute KLD_PR_OFFLINE_AUDIT")

    def deny(kind: str):
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(fd, ("BLOCKED " + kind + "\n").encode("ascii"))
        finally:
            os.close(fd)
        raise RuntimeError("KLD PR CI external network access prohibited: " + kind)

    def hook(name):
        def inner(*args, **kwargs):
            deny(name)
        return inner

    socket.getaddrinfo = hook("socket.getaddrinfo")
    socket.gethostbyname = hook("socket.gethostbyname")
    socket.gethostbyname_ex = hook("socket.gethostbyname_ex")
    socket.gethostbyaddr = hook("socket.gethostbyaddr")
    socket.socket.connect = hook("socket.connect")
    socket.socket.connect_ex = hook("socket.connect_ex")
    socket.socket.sendto = hook("socket.sendto")
    socket.create_connection = hook("socket.create_connection")

    # PR regression suite legitimately invokes only read-only local git ls-files.
    # No shell, curl, wget, or nested Python scripts are permitted as subprocesses.
    original_popen = subprocess.Popen

    def guarded_popen(args, *popen_args, **popen_kwargs):
        if (
            isinstance(args, (tuple, list))
            and tuple(args) == ("git", "ls-files")
            and not popen_kwargs.get("shell", False)
        ):
            return original_popen(args, *popen_args, **popen_kwargs)
        deny("subprocess.Popen")

    subprocess.Popen = guarded_popen
    os.system = hook("os.system")
    os.popen = hook("os.popen")
