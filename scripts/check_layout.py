#!/usr/bin/env python3
"""Check the Plugin repository in the working directory follows the NSISDIR-shaped layout.

Configured through the environment variable NAME, set by action.yml.
"""

import sys
from pathlib import Path

from common import env, find_license, is_doc
from toolchain import SOURCE_EXTS

# Folders that mirror NSISDIR, spelled the way NSIS spells them
KNOWN_DIRS = ("Contrib", "Docs", "Examples", "Include", "Plugins")


def check(root, name):
    """(errors, suggestions), each a list of (path relative to root, message)."""
    errors, suggestions = [], []
    top = {p.name: p for p in root.iterdir()}

    for known in KNOWN_DIRS:
        for entry in top:
            if entry != known and entry.lower() == known.lower():
                errors.append((entry, f"rename {entry}/ to {known}/"))

    if "Plugins" in top:
        errors.append(("Plugins", "Plugins/ is built by the action, don't commit it"))
    for dll in sorted(root.rglob("*.dll")):
        rel = dll.relative_to(root)
        if not any(part.startswith(".") for part in rel.parts):
            errors.append((rel.as_posix(), "don't commit DLLs, the action builds them"))

    contrib = top.get("Contrib")
    subdirs = {p.name: p for p in contrib.iterdir()} if contrib else {}
    if name not in subdirs:
        near = [s for s in subdirs if s.lower() == name.lower()]
        errors.append(
            (f"Contrib/{name}", f"rename Contrib/{near[0]}/ to Contrib/{name}/")
            if near
            else (f"Contrib/{name}", f"put the source in Contrib/{name}/")
        )
    elif not any(p.suffix.lower() in SOURCE_EXTS for p in subdirs[name].rglob("*")):
        errors.append(
            (
                f"Contrib/{name}",
                f"Contrib/{name}/ has no source ({', '.join(sorted(SOURCE_EXTS))})",
            )
        )

    for folder in ("Docs", "Examples"):
        for entry in sorted(top[folder].iterdir()) if folder in top else ():
            if entry.name != name:
                errors.append(
                    (
                        f"{folder}/{entry.name}",
                        f"move {folder}/{entry.name} into {folder}/{name}/",
                    )
                )

    if find_license(root) is None:
        errors.append(
            (
                "LICENSE",
                f"add a LICENSE at the top level or in Docs/{name}/, the installer shows it",
            )
        )
    if not any(is_doc(p, ("README",)) for p in top.values()):
        suggestions.append(
            ("README.md", "consider a top-level README, it ships in the archive")
        )
    return errors, suggestions


def main():
    name = env("NAME")
    if not name:
        print("::error::name is required", flush=True)
        return 1
    errors, suggestions = check(Path.cwd(), name)
    for level, findings in (("error", errors), ("notice", suggestions)):
        for path, message in findings:
            print(f"::{level} file={path}::{message}")
    print(f"{len(errors)} error(s), {len(suggestions)} suggestion(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
