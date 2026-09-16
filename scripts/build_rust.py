#!/usr/bin/env python3
"""Compile a Rust NSIS plugin with Cargo for one or more targets.

Run through toolchain.py, which validates the Toolchain and dispatches here:
  resolve  write step outputs (version, output-dir)
  build    add the Rust targets, compile every target
"""

import json
import shutil
import subprocess
from pathlib import Path

from common import (
    PINNED_NSIS_VERSION,
    TARGETS,
    BuildError,
    env,
    run,
    set_output,
    split,
    verify_dll,
)

RUST_TARGET = {
    "x86": "i686-pc-windows-msvc",
    "amd64": "x86_64-pc-windows-msvc",
    "arm64": "aarch64-pc-windows-msvc",
}
# Unicode targets build the crate's default features; x86-ansi swaps them for this one
ANSI_FEATURE = "ansi"


def check_inputs(targets):
    if not targets:
        raise BuildError("no targets")
    for target in targets:
        if target not in TARGETS:
            raise BuildError(f"unknown target '{target}'")


def find_manifest(path):
    manifest = Path(path)
    if manifest.name != "Cargo.toml":
        raise BuildError(f"project must be a Cargo.toml, got '{path}'")
    if not manifest.is_file():
        raise BuildError(f"project '{path}' does not exist")
    return manifest


def cargo_command(manifest, target, target_dir):
    arch, unicode, _ = TARGETS[target]
    cmd = [
        "cargo",
        "build",
        "--release",
        "--manifest-path",
        manifest,
        "--target",
        RUST_TARGET[arch],
        "--target-dir",
        target_dir,
        # JSON on stdout to find the DLL, human-readable errors on stderr
        "--message-format=json-render-diagnostics",
    ]
    if not unicode:
        cmd += ["--no-default-features", "--features", ANSI_FEATURE]
    return cmd


def find_dll(messages, manifest):
    """The cdylib Cargo built for the project's own package, not for a dependency."""
    dlls = [
        Path(f)
        for m in messages
        if m.get("reason") == "compiler-artifact"
        and Path(m["manifest_path"]) == manifest
        and "cdylib" in m["target"]["kind"]
        for f in m["filenames"]
        if f.lower().endswith(".dll")
    ]
    if len(dlls) != 1:
        raise BuildError(
            f'{manifest} builds no cdylib, set crate-type = ["cdylib"] under [lib]'
        )
    return dlls[0]


def resolve():
    check_inputs(split(env("TARGETS", "x86-unicode,amd64-unicode")))
    # No Plugin API to fetch, but the installer is still compiled with the pinned NSIS
    set_output("version", PINNED_NSIS_VERSION)
    set_output("output-dir", Path(env("OUTPUT_DIR", "out")).resolve())


def build():
    targets = split(env("TARGETS", "x86-unicode,amd64-unicode"))
    check_inputs(targets)
    name = env("NAME")
    if not name:
        raise BuildError("name is required")
    output = Path(env("OUTPUT_DIR", "out")).resolve()
    manifest = find_manifest(env("PROJECT")).resolve()
    target_dir = output / "obj"

    # From the project, so a rust-toolchain.toml there picks the toolchain
    triples = sorted({RUST_TARGET[TARGETS[t][0]] for t in targets})
    run(["rustup", "target", "add", *triples], cwd=manifest.parent)

    for target in targets:
        machine = TARGETS[target][2]
        print(f"::group::rust {target}", flush=True)
        # Cargo writes the artifact JSON to stdout, the rendered diagnostics to stderr
        result = run(
            cargo_command(manifest, target, target_dir),
            cwd=manifest.parent,
            stdout=subprocess.PIPE,
            text=True,
        )
        messages = [json.loads(line) for line in result.stdout.splitlines() if line]

        dll = output / "Plugins" / target / f"{name}.dll"
        dll.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(find_dll(messages, manifest), dll)
        verify_dll(dll, machine)
        print("::endgroup::", flush=True)

    shutil.rmtree(target_dir, ignore_errors=True)
