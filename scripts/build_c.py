#!/usr/bin/env python3
"""Compile a C/C++ NSIS plugin with MSVC or MinGW for one or more targets.

Run through toolchain.py, which validates the Toolchain and dispatches here:
  resolve  write step outputs (version, plugin-api, output-dir)
  build    fetch the Plugin API if missing, compile every target
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from common import (
    PINNED_NSIS_VERSION,
    TARGETS,
    BuildError,
    env,
    fetch_plugin_api,
    run,
    set_output,
    split,
    verify_dll,
)
from toolchain import C_EXTS, CXX_EXTS

MINGW_PREFIX = {"x86": "i686-w64-mingw32", "amd64": "x86_64-w64-mingw32"}
MSVC_VCVARS = {"x86": "x64_x86", "amd64": "x64", "arm64": "x64_arm64"}
# Linked into every Plugin; an unused import library adds no import to the DLL.
# Covers 110 of 118 corpus Plugins; add to it rather than taking a libs input.
LIBS = (
    "kernel32",
    "user32",
    "advapi32",
    "gdi32",
    "shell32",
    "ole32",
    "oleaut32",
    "uuid",
    "comctl32",
    "comdlg32",
    "shlwapi",
    "version",
    "wininet",
    "winmm",
    "ws2_32",
    "wsock32",
    "setupapi",
    "netapi32",
    "userenv",
    "winhttp",
    "rpcrt4",
    "cabinet",
)


def check_inputs(toolchain, targets, crt):
    if crt not in ("static", "none"):
        raise BuildError(f"unknown crt '{crt}' (static, none)")
    if not targets:
        raise BuildError("no targets")
    for target in targets:
        if target not in TARGETS:
            raise BuildError(f"unknown target '{target}'")
        if toolchain == "mingw" and TARGETS[target][0] not in MINGW_PREFIX:
            raise BuildError(f"{target} is not supported with mingw, use msvc")


def expand_sources(patterns):
    """Returns (compile sources, resource scripts, has C++)."""
    sources, resources, cxx = [], [], False
    for pattern in patterns:
        matches = sorted(glob.glob(pattern, recursive=True))
        if not matches:
            raise BuildError(f"no source matches '{pattern}'")
        for match in matches:
            ext = Path(match).suffix.lower()
            if ext == ".rc":
                resources.append(match)
            elif ext in C_EXTS | CXX_EXTS:
                sources.append(match)
                cxx = cxx or ext in CXX_EXTS
            else:
                raise BuildError(f"unsupported source '{match}'")
    if not sources:
        raise BuildError("no C/C++ sources")
    return sources, resources, cxx


def include_dirs(files):
    """Every directory holding a matched source, so headers next to any of them are found."""
    return sorted({str(Path(f).parent) for f in files})


def msvc_env(vcvars):
    vswhere = (
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft Visual Studio/Installer/vswhere.exe"
    )
    vs = subprocess.run(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not vs:
        raise BuildError("no Visual Studio with C++ tools found")

    vcvarsall = Path(vs) / "VC/Auxiliary/Build/vcvarsall.bat"
    result = subprocess.run(
        f'call "{vcvarsall}" {vcvars} >nul && set',
        shell=True,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BuildError(f"vcvarsall {vcvars} failed")
    return dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
        if "=" in line and not line.startswith("=")
    )


def build_msvc(cfg, arch, unicode, dll, objdir):
    vcenv = msvc_env(MSVC_VCVARS[arch])

    # CreateProcess looks commands up on our PATH, not on the one passed in env
    def tool(name):
        path = shutil.which(name, path=vcenv.get("PATH") or vcenv.get("Path"))
        if not path:
            raise BuildError(f"{name} not found after vcvarsall")
        return path

    plugin_api = cfg["plugin_api"]
    flags = ["/DWIN32", "/D_WINDOWS", f"/I{plugin_api}", f"/I{plugin_api / 'nsis'}"]
    if unicode:
        flags += ["/DUNICODE", "/D_UNICODE"]
    flags += [f"/I{d}" for d in cfg["include_dirs"]]

    res = []
    for rc in cfg["resources"]:
        out = objdir / (Path(rc).stem + ".res")
        run([tool("rc"), "/nologo", *flags, f"/fo{out}", rc], env=vcenv)
        res.append(out)

    compile_flags = [
        "/nologo",
        "/O1",
        "/GS-",
        "/W3",
        "/MT" if cfg["crt"] == "static" else "/Zl",
        *flags,
    ]

    # As a library the Plugin API is only linked when referenced, so Plugins that bring their own stack functions don't collide with it
    api_obj, api_lib = objdir / "nsis-pluginapi.obj", objdir / "nsis-pluginapi.lib"
    run([tool("cl"), *compile_flags, "/c", cfg["api"], f"/Fo{api_obj}"], env=vcenv)
    run([tool("lib"), "/nologo", f"/OUT:{api_lib}", api_obj], env=vcenv)

    link = [
        "/link",
        f"/IMPLIB:{objdir / cfg['name']}.lib",
        *[f"{lib}.lib" for lib in LIBS],
    ]
    if cfg["crt"] == "none":
        link.append("/NODEFAULTLIB")
        # /Zl predefines _VC_NODEFAULTLIB, which Plugins use to rename DllMain to the linker's default entry
        if not any(
            "_DllMainCRTStartup" in Path(s).read_text(errors="ignore")
            for s in cfg["sources"]
        ):
            link.append("/ENTRY:DllMain")
    else:
        # A Plugin defining its own _DllMainCRTStartup keeps libcmt's startup object,
        # and with it these defaultlib references, out of the link
        link += ["libvcruntime.lib", "libucrt.lib"]

    run(
        [
            tool("cl"),
            "/LD",
            *compile_flags,
            *cfg["sources"],
            api_lib,
            *res,
            f"/Fo{objdir}{os.sep}",
            f"/Fe{dll}",
            *link,
        ],
        env=vcenv,
    )


def build_mingw(cfg, arch, unicode, dll, objdir):
    prefix = MINGW_PREFIX[arch]
    if not shutil.which(f"{prefix}-gcc"):
        if not sys.platform.startswith("linux"):
            raise BuildError(f"{prefix}-gcc not found")
        run(["sudo", "apt-get", "update"])
        run(
            ["sudo", "apt-get", "install", "-y", "--no-install-recommends", "mingw-w64"]
        )

    plugin_api = cfg["plugin_api"]
    flags = ["-DWIN32", "-D_WINDOWS", "-I", plugin_api, "-I", plugin_api / "nsis"]
    if unicode:
        flags += ["-DUNICODE", "-D_UNICODE"]
    for d in cfg["include_dirs"]:
        flags += ["-I", d]

    res = []
    for rc in cfg["resources"]:
        out = objdir / (Path(rc).stem + ".res.o")
        run([f"{prefix}-windres", *flags, "-O", "coff", "-o", out, rc])
        res.append(out)

    link = [f"-l{lib}" for lib in LIBS]
    if cfg["crt"] == "static":
        link += ["-static"] + (["-lstdc++"] if cfg["cxx"] else [])
    else:
        entry = "_DllMain@12" if arch == "x86" else "DllMain"
        link += ["-nostdlib", f"-Wl,-e,{entry}", "-lgcc"]

    # Plugins that declare the Plugin API globals themselves link under MSVC; GCC 10+ needs -fcommon to match
    compile_flags = ["-Os", "-fcommon", *flags]

    # As an archive the Plugin API is only linked when referenced, so Plugins that bring their own stack functions don't collide with it
    api_obj, api_lib = objdir / "nsis-pluginapi.o", objdir / "libnsis-pluginapi.a"
    run([f"{prefix}-gcc", *compile_flags, "-c", cfg["api"], "-o", api_obj])
    run([f"{prefix}-ar", "rcs", api_lib, api_obj])

    run(
        [
            f"{prefix}-gcc",
            "-shared",
            "-Wl,--kill-at",
            *compile_flags,
            *cfg["sources"],
            api_lib,
            *res,
            "-o",
            dll,
            *link,
        ]
    )


def resolve():
    toolchain, targets, crt = (
        env("TOOLCHAIN", "msvc"),
        split(env("TARGETS", "x86-unicode,amd64-unicode")),
        env("CRT", "static"),
    )
    check_inputs(toolchain, targets, crt)
    version = PINNED_NSIS_VERSION
    set_output("version", version)
    set_output(
        "plugin-api",
        Path(env("RUNNER_TEMP", tempfile.gettempdir())) / f"nsis-plugin-api-{version}",
    )
    set_output("output-dir", Path(env("OUTPUT_DIR", "out")).resolve())


def build():
    toolchain, targets, crt = (
        env("TOOLCHAIN", "msvc"),
        split(env("TARGETS", "x86-unicode,amd64-unicode")),
        env("CRT", "static"),
    )
    check_inputs(toolchain, targets, crt)
    name = env("NAME")
    if not name:
        raise BuildError("name is required")
    plugin_api = Path(
        env("PLUGIN_API")
        or Path(tempfile.gettempdir()) / f"nsis-plugin-api-{PINNED_NSIS_VERSION}"
    )
    output = Path(env("OUTPUT_DIR", "out"))

    sources, resources, cxx = expand_sources(split(env("SOURCES")))
    fetch_plugin_api(PINNED_NSIS_VERSION, plugin_api)

    cfg = {
        "name": name,
        "plugin_api": plugin_api,
        "crt": crt,
        "sources": sources,
        "resources": resources,
        "cxx": cxx,
        "api": plugin_api / "nsis" / "pluginapi.c",
        "include_dirs": include_dirs(sources + resources),
    }
    builder = build_msvc if toolchain == "msvc" else build_mingw

    for target in targets:
        arch, unicode, machine = TARGETS[target]
        print(f"::group::{toolchain} {target}", flush=True)
        dll = output / "Plugins" / target / f"{name}.dll"
        objdir = output / "obj" / target
        dll.parent.mkdir(parents=True, exist_ok=True)
        objdir.mkdir(parents=True, exist_ok=True)

        builder(cfg, arch, unicode, dll, objdir)

        verify_dll(dll, machine)
        print("::endgroup::", flush=True)

    shutil.rmtree(output / "obj", ignore_errors=True)
