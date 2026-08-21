"""Lightweight console entry point.

Keep commands that only need package metadata away from the much larger CLI
module and its optional runtime dependencies. All other invocations are
delegated unchanged.
"""
from __future__ import annotations

import sys

from termux_agent import __version__


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--version"], ["--version", "--json"], ["--json", "--version"]):
        if "--json" in args:
            print(f'{{"name": "termux-agent", "version": "{__version__}"}}')
        else:
            print(f"termux-agent {__version__}")
        return 0

    from termux_agent.cli import main as cli_main

    return cli_main(args)
