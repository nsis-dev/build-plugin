#!/usr/bin/env python3
"""Compile a C/C++ NSIS plugin with MSVC, MinGW or Zig for one or more targets.

Run through toolchain.py, which validates the Toolchain and dispatches here:
  resolve  write step outputs (version, plugin-api, output-dir, and zig-version, zig-dir for zig)
  build    fetch the Plugin API and Zig if missing, compile every target
"""

import glob
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from common import (
    PINNED_NSIS_VERSION,
    TARGETS,
    BuildError,
    download,
    env,
    fetch_plugin_api,
    run,
    set_output,
    split,
    verify_dll,
)
from toolchain import C_EXTS, CXX_EXTS

# Bumped deliberately, like the Plugin API version
PINNED_ZIG_VERSION = "0.16.0"

MINGW_PREFIX = {"x86": "i686-w64-mingw32", "amd64": "x86_64-w64-mingw32"}
MSVC_VCVARS = {"x86": "x64_x86", "amd64": "x64", "arm64": "x64_arm64"}
ZIG_ARCH = {"x86": "x86", "amd64": "x86_64", "arm64": "aarch64"}
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


def gnu_flags(cfg, unicode):
    """Defines and include directories, spelled the way gcc, clang and windres take them."""
    plugin_api = cfg["plugin_api"]
    flags = ["-DWIN32", "-D_WINDOWS", "-I", plugin_api, "-I", plugin_api / "nsis"]
    if unicode:
        flags += ["-DUNICODE", "-D_UNICODE"]
    for d in cfg["include_dirs"]:
        flags += ["-I", d]
    return flags


def zig_release(index, version, host):
    """(url, sha256) of a Zig release for a host, from ziglang.org's download index."""
    try:
        entry = index[version][host]
    except KeyError:
        raise BuildError(f"Zig {version} has no download for {host}")
    return entry["tarball"], entry["shasum"]


def zig_host():
    """The host the way ziglang.org names its downloads, e.g. x86_64-linux."""
    arch = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }
    system = {"linux": "linux", "win32": "windows", "darwin": "macos"}
    machine = arch.get(platform.machine().lower())
    if not machine or sys.platform not in system:
        raise BuildError(f"no Zig download for {platform.machine()} {sys.platform}")
    return f"{machine}-{system[sys.platform]}"


def install_zig(version, zig_dir):
    """Unpacks the official Zig release into zig_dir unless it is already there."""
    zig_dir = Path(zig_dir)
    zig = zig_dir / ("zig.exe" if sys.platform == "win32" else "zig")
    if zig.is_file():
        return zig

    # ponytail: ziglang.org only; switch to its community mirrors if CI traffic ever gets throttled
    index = json.loads(download("https://ziglang.org/download/index.json"))
    url, sha256 = zig_release(index, version, zig_host())
    data = download(url)
    if hashlib.sha256(data).hexdigest() != sha256:
        raise BuildError(f"{url} does not match ziglang.org's SHA-256 {sha256}")

    zig_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(zig_dir, ignore_errors=True)
    with tempfile.TemporaryDirectory(dir=zig_dir.parent) as tmp:
        if url.endswith(".zip"):
            zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tar:
                tar.extractall(tmp, filter="data")
        # The archive holds one folder, zig-<host>-<version>
        (top,) = Path(tmp).iterdir()
        top.rename(zig_dir)
    if not zig.is_file():
        raise BuildError(f"Zig {version} archive lacks {zig.name}")
    return zig


def build_zig(cfg, arch, unicode, dll, objdir):
    zig = cfg["zig"]
    # Next to Zig itself, so the cache step keeps the mingw-w64 CRT Zig builds once per target
    zig_env = {**os.environ, "ZIG_GLOBAL_CACHE_DIR": str(zig.parent / "cache")}
    target = ["-target", f"{ZIG_ARCH[arch]}-windows-gnu"]
    cc, cxx = [zig, "cc", *target], [zig, "c++", *target]
    flags = gnu_flags(cfg, unicode)

    res = []
    for rc in cfg["resources"]:
        out = objdir / (Path(rc).stem + ".res")
        run([zig, "rc", *flags, "--", rc, out], env=zig_env)
        res.append(out)

    # Otherwise lld-link writes the import library next to the DLL, into the Release Archive
    implib = [f"-Wl,--out-implib,{objdir / cfg['name']}.lib"]
    if cfg["crt"] == "static":
        link = implib + [f"-l{lib}" for lib in LIBS]
    else:
        # lld-link adds the x86 underscore itself, unlike the mingw linker
        entry = "DllMain@12" if arch == "x86" else "DllMain"
        # Zig's uuid pulls in mingw-w64's pseudo-reloc.c, which needs the CRT; define GUIDs with INITGUID instead
        link = implib + [f"-l{lib}" for lib in LIBS if lib != "uuid"]
        link += ["-nostdlib", f"-Wl,-e,{entry}"]

    # Compiled apart from the link: zig cc -nostdlib also drops the mingw-w64 headers.
    # Per file, so .c stays C (zig c++ would mangle its exports) and only C++ gets libc++'s headers.
    compile_flags = ["-Os", "-fcommon", *flags]
    objs = []
    for i, src in enumerate(cfg["sources"]):
        obj = objdir / f"{i}-{Path(src).stem}.o"
        driver = cxx if Path(src).suffix.lower() in CXX_EXTS else cc
        run([*driver, *compile_flags, "-c", src, "-o", obj], env=zig_env)
        objs.append(obj)

    api_obj, api_lib = objdir / "nsis-pluginapi.o", objdir / "libnsis-pluginapi.a"
    run([*cc, *compile_flags, "-c", cfg["api"], "-o", api_obj], env=zig_env)
    run([zig, "ar", "rcs", api_lib, api_obj], env=zig_env)

    # No --kill-at: Zig's lld has no such flag, and like MSVC it keeps stdcall exports decorated
    # -Os again: the link's optimize mode decides how Zig builds the CRT and compiler-rt it adds.
    # zig c++ links libc++ in statically.
    linker = cxx if cfg["cxx"] else cc
    run(
        [*linker, "-shared", "-Os", *objs, api_lib, *res, "-o", dll, *link], env=zig_env
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

    flags = gnu_flags(cfg, unicode)

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
    temp = Path(env("RUNNER_TEMP", tempfile.gettempdir()))
    set_output("version", version)
    set_output("plugin-api", temp / f"nsis-plugin-api-{version}")
    if toolchain == "zig":
        set_output("zig-version", PINNED_ZIG_VERSION)
        set_output("zig-dir", temp / f"zig-{PINNED_ZIG_VERSION}")
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
    if toolchain == "zig":
        zig_dir = (
            env("ZIG_DIR") or Path(tempfile.gettempdir()) / f"zig-{PINNED_ZIG_VERSION}"
        )
        cfg["zig"] = install_zig(PINNED_ZIG_VERSION, Path(zig_dir).resolve())
    builder = {"msvc": build_msvc, "mingw": build_mingw, "zig": build_zig}[toolchain]

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
