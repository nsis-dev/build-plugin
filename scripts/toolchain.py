#!/usr/bin/env python3
"""What each Toolchain needs, and the one entry point action.yml calls.

  toolchain.py resolve  validate the inputs, write the step outputs
  toolchain.py build    compile every target

Everything that differs per Toolchain and is not the compiler invocation itself
lives in TOOLCHAINS, so no caller has to switch on the Toolchain name again.
"""

import importlib
import sys
from typing import NamedTuple

from common import BuildError, cli, env, set_output

C_EXTS = {".c"}
CXX_EXTS = {".cpp", ".cxx", ".cc"}
PASCAL_EXTS = {".dpr", ".lpr", ".pas"}


class Toolchain(NamedTuple):
    script: str  # module holding its resolve() and build()
    runner_os: str  # the only runner it works on, "" for any
    takes: str  # "sources" or "project"
    exts: frozenset  # what its sources look like, for the layout check
    crt: bool  # whether the crt input means anything to it


TOOLCHAINS = {
    "msvc": Toolchain("build_c", "Windows", "sources", C_EXTS | CXX_EXTS, True),
    "mingw": Toolchain("build_c", "Linux", "sources", C_EXTS | CXX_EXTS, True),
    "zig": Toolchain("build_c", "", "sources", C_EXTS | CXX_EXTS, True),
    "fpc": Toolchain("build_pascal", "Windows", "project", PASCAL_EXTS, False),
    "rust": Toolchain("build_rust", "Windows", "project", {".rs"}, False),
}
SOURCE_EXTS = frozenset().union(*(t.exts for t in TOOLCHAINS.values()))


def check_inputs(name, sources, project, runner_os=""):
    """A Toolchain runs on one OS, or any, and takes exactly one of sources or project."""
    if name not in TOOLCHAINS:
        raise BuildError(f"unknown toolchain '{name}' ({', '.join(TOOLCHAINS)})")
    toolchain = TOOLCHAINS[name]
    if runner_os and toolchain.runner_os and runner_os != toolchain.runner_os:
        raise BuildError(f"toolchain {name} needs a {toolchain.runner_os} runner")

    given = {"sources": sources, "project": project}
    other = "project" if toolchain.takes == "sources" else "sources"
    if given[other]:
        raise BuildError(f"{name} takes {toolchain.takes}, not {other}")
    if not given[toolchain.takes]:
        raise BuildError(f"{toolchain.takes} is required for {name}")
    return toolchain


def main(command):
    name = env("TOOLCHAIN", "msvc")
    toolchain = check_inputs(name, env("SOURCES"), env("PROJECT"), env("RUNNER_OS"))
    if command == "resolve":
        # So the later action.yml steps reuse this interpreter instead of guessing
        set_output("python", sys.executable)
        # Only a non-default crt can have come from the workflow, so only that is worth saying
        if not toolchain.crt and env("CRT", "static") != "static":
            print(
                f"::warning::{name} ignores crt, it applies to msvc, mingw and zig",
                flush=True,
            )
    getattr(importlib.import_module(toolchain.script), command)()


if __name__ == "__main__":
    cli(resolve=lambda: main("resolve"), build=lambda: main("build"))
