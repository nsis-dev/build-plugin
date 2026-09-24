#!/usr/bin/env python3
"""Compile installer.nsi for a Release Archive tree.

Configured through environment variables set by action.yml.
"""

import shutil
from pathlib import Path

import common
from common import BuildError, env, guard, run, set_output


def find_license(tree):
    """The license shown on the installer's page; a Plugin is required to have one."""
    license = common.find_license(tree)
    if license is None:
        raise BuildError(f"{tree} has no LICENSE, the installer shows it on its page")
    return license


def main():
    name, version, tree = env("NAME"), env("VERSION"), env("TREE")
    if not (name and version and tree):
        raise BuildError("name, version and tree are required")
    # makensis resolves relative paths against the script, not the working directory
    tree = Path(tree).resolve()
    if not tree.is_dir():
        raise BuildError(f"{tree} does not exist")

    installer = (
        Path(env("OUTPUT_DIR", "dist")).resolve() / f"{name}-{version}-setup.exe"
    )
    installer.parent.mkdir(parents=True, exist_ok=True)
    makensis = shutil.which("makensis")
    if not makensis:
        raise BuildError("makensis not found")

    cmd = [
        makensis,
        "-V2",
        f"-DNAME={name}",
        f"-DVERSION={version}",
        f"-DSRC={tree}",
        f"-DOUTFILE={installer}",
    ]
    cmd.append(f"-DLICENSE={find_license(tree)}")
    cmd.append(str(Path(__file__).with_suffix(".nsi")))
    run(cmd)

    set_output("installer", installer)


if __name__ == "__main__":
    guard(main)
