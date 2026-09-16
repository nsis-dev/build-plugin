#!/usr/bin/env python3
"""Compile a Pascal NSIS plugin with Free Pascal for one or more targets.

Run through toolchain.py, which validates the Toolchain and dispatches here:
  resolve  write step outputs (version, plugin-api, fpc-dir, output-dir)
  build    install Free Pascal and fetch the Plugin API if missing, compile every target
"""

import hashlib
import shutil
import tempfile
from pathlib import Path

from common import (
    PINNED_NSIS_VERSION,
    TARGETS,
    BuildError,
    download,
    env,
    fetch_plugin_api,
    rss_md5,
    run,
    set_output,
    split,
    verify_dll,
)
from toolchain import PASCAL_EXTS

# Bumped deliberately, like the Plugin API version
PINNED_FPC_VERSION = "3.2.2"

# arch: (compiler, unit folder); Free Pascal 3.2 has no aarch64-win64
FPC_ARCH = {"x86": ("ppc386", "i386-win32"), "amd64": ("ppcrossx64", "x86_64-win64")}


def check_inputs(targets):
    if not targets:
        raise BuildError("no targets")
    for target in targets:
        if target not in TARGETS:
            raise BuildError(f"unknown target '{target}'")
        if TARGETS[target][0] not in FPC_ARCH:
            raise BuildError(f"{target} is not supported by Free Pascal")


def find_project(path):
    """Free Pascal compiles one project file and finds its units itself."""
    project = Path(path)
    if project.suffix.lower() not in PASCAL_EXTS:
        raise BuildError(f"project must be a .dpr, .lpr or .pas file, got '{path}'")
    if not project.is_file():
        raise BuildError(f"project '{path}' does not exist")
    return project


def install_fpc(version, fpc_dir):
    """Runs the official win32+win64 installer into fpc_dir unless it is already there."""
    fpc_dir = Path(fpc_dir)
    bin_dir = fpc_dir / "bin" / "i386-win32"
    if not (bin_dir / "ppc386.exe").is_file():
        folder = f"Win32/{version}"
        filename = f"fpc-{version}.win32.and.win64.exe"
        md5 = rss_md5(
            download(
                f"https://sourceforge.net/projects/freepascal/rss?path=/{folder}"
            ).decode("utf-8", "replace"),
            filename,
        )
        data = download(
            f"https://downloads.sourceforge.net/project/freepascal/{folder}/{filename}"
        )
        if hashlib.md5(data).hexdigest() != md5:
            raise BuildError(f"{filename} does not match SourceForge's MD5 {md5}")
        setup = Path(tempfile.gettempdir()) / filename
        setup.write_bytes(data)
        run(
            [
                setup,
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/SP-",
                "/NORESTART",
                f"/DIR={fpc_dir}",
            ]
        )

    for compiler, units in FPC_ARCH.values():
        for path in (bin_dir / f"{compiler}.exe", fpc_dir / "units" / units / "rtl"):
            if not path.exists():
                raise BuildError(f"Free Pascal installation lacks {path}")
    return bin_dir


def fpc_command(cfg, arch, unicode, dll, objdir):
    compiler, units = FPC_ARCH[arch]
    cmd = [
        cfg["bin"] / f"{compiler}.exe",
        "-n",  # ignore fpc.cfg, everything is on the command line
        "-Mdelphi",  # the corpus is Delphi code; {$mode} in a source still wins
        "-O2",
        "-XX",
        "-CX",
        "-Xs",
        f"-Fu{cfg['fpc_dir'] / 'units' / units / '*'}",
        f"-Fu{cfg['plugin_api'] / 'nsis'}",
    ]
    if unicode:
        cmd.append("-dUNICODE")
    cmd += [f"-FU{objdir}", f"-FE{dll.parent}", f"-o{dll.name}"]
    return cmd + [cfg["project"]]


def resolve():
    check_inputs(split(env("TARGETS", "x86-unicode,amd64-unicode")))
    temp = Path(env("RUNNER_TEMP", tempfile.gettempdir()))
    set_output("version", PINNED_NSIS_VERSION)
    set_output("plugin-api", temp / f"nsis-plugin-api-{PINNED_NSIS_VERSION}")
    set_output("fpc-version", PINNED_FPC_VERSION)
    set_output("fpc-dir", temp / f"fpc-{PINNED_FPC_VERSION}")
    set_output("output-dir", Path(env("OUTPUT_DIR", "out")).resolve())


def build():
    targets = split(env("TARGETS", "x86-unicode,amd64-unicode"))
    check_inputs(targets)
    name = env("NAME")
    if not name:
        raise BuildError("name is required")
    temp = Path(tempfile.gettempdir())
    plugin_api = Path(
        env("PLUGIN_API") or temp / f"nsis-plugin-api-{PINNED_NSIS_VERSION}"
    )
    fpc_dir = Path(env("FPC_DIR") or temp / f"fpc-{PINNED_FPC_VERSION}")
    output = Path(env("OUTPUT_DIR", "out")).resolve()

    project = find_project(env("PROJECT")).resolve()
    fetch_plugin_api(PINNED_NSIS_VERSION, plugin_api)

    cfg = {
        "project": project,
        "plugin_api": plugin_api,
        "fpc_dir": fpc_dir,
        "bin": install_fpc(PINNED_FPC_VERSION, fpc_dir),
    }

    for target in targets:
        arch, unicode, machine = TARGETS[target]
        print(f"::group::fpc {target}", flush=True)
        dll = output / "Plugins" / target / f"{name}.dll"
        objdir = output / "obj" / target
        dll.parent.mkdir(parents=True, exist_ok=True)
        objdir.mkdir(parents=True, exist_ok=True)

        # Free Pascal resolves units relative to the project, so compile from there
        run(fpc_command(cfg, arch, unicode, dll, objdir), cwd=project.parent)

        verify_dll(dll, machine)
        print("::endgroup::", flush=True)

    shutil.rmtree(output / "obj", ignore_errors=True)
