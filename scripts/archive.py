#!/usr/bin/env python3
"""Package the built plugin DLLs and everything else a Plugin ships into an NSISDIR-shaped zip.

Configured through environment variables set by action.yml.
"""

import shutil
import tempfile
import zipfile
from pathlib import Path

from common import BuildError, env, guard, is_doc, set_output

# The NSISDIR folders a Plugin may ship besides the built Plugins/, in the layout
# check_layout.py enforces. They go into NSISDIR next to the DLLs, so they ship too.
SHIPPED_DIRS = ("Docs", "Examples", "Include")


def resolve_version(ref_type, ref_name, sha):
    """The tag without a leading v; builds of a branch or pull request use the short commit SHA."""
    version = ref_name.removeprefix("v") if ref_type == "tag" else sha[:7]
    if not version:
        raise BuildError("no tag or commit to take the version from")
    return version


def stage(tree, plugins, root):
    """The built Plugins/, root's SHIPPED_DIRS, and the LICENSE and README files at its top."""
    if not (plugins / "Plugins").is_dir():
        raise BuildError(f"{plugins / 'Plugins'} does not exist")
    shutil.rmtree(tree, ignore_errors=True)
    tree.mkdir(parents=True)
    shutil.copytree(plugins / "Plugins", tree / "Plugins")
    for folder in SHIPPED_DIRS:
        if (root / folder).is_dir():
            shutil.copytree(root / folder, tree / folder)
    for path in root.iterdir():
        if is_doc(path):
            shutil.copy2(path, tree)


def write_zip(tree, archive):
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(tree.rglob("*")):
            z.write(path, path.relative_to(tree).as_posix())


def main():
    name = env("NAME")
    if not name:
        raise BuildError("name is required")
    version = resolve_version(
        env("GITHUB_REF_TYPE"), env("GITHUB_REF_NAME"), env("GITHUB_SHA")
    )
    tree = Path(env("RUNNER_TEMP", tempfile.gettempdir())) / f"archive-{name}"
    archive = Path(env("OUTPUT_DIR", "dist")).resolve() / f"{name}-{version}.zip"

    stage(tree, Path(env("PLUGINS_DIR")), Path.cwd())
    write_zip(tree, archive)

    set_output("archive", archive)
    set_output("tree", tree)
    set_output("version", version)


if __name__ == "__main__":
    guard(main)
