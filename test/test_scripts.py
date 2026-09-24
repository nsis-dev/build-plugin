#!/usr/bin/env python3
"""Self-check for the action scripts. Run: python3 test/test_scripts.py"""

# The scripts are imported after scripts/ is put on the path

import os
import re
import struct
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import archive
import build_c as build
import build_pascal as pascal
import build_rust as rust
import check_layout
import common
import installer
import toolchain


def raises(error, fn, *args):
    try:
        fn(*args)
    except error:
        return
    raise AssertionError(f"{fn.__name__}{args} did not raise {error.__name__}")


def test_split():
    assert common.split("a, b\nc d,,") == ["a", "b", "c d"]
    assert common.split("") == []


def test_check_inputs():
    build.check_inputs("mingw", ["x86-ansi", "amd64-unicode"], "static")
    build.check_inputs("msvc", ["arm64-unicode"], "none")
    raises(build.BuildError, build.check_inputs, "mingw", ["arm64-unicode"], "static")
    raises(build.BuildError, build.check_inputs, "msvc", ["x64-ansi"], "static")
    raises(build.BuildError, build.check_inputs, "msvc", ["x86-ansi"], "dynamic")
    raises(build.BuildError, build.check_inputs, "msvc", [], "static")


def test_expand_sources():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for f in ("src/a.c", "src/sub dir/b.cpp", "src/res.rc", "src/notes.txt"):
            (tmp / f).parent.mkdir(parents=True, exist_ok=True)
            (tmp / f).write_text("")
        sources, resources, cxx = build.expand_sources(
            [f"{tmp}/src/**/*.c*", f"{tmp}/src/*.rc"]
        )
        assert [Path(s).name for s in sources] == ["a.c", "b.cpp"], sources
        assert [Path(r).name for r in resources] == ["res.rc"]
        assert cxx
        assert build.expand_sources([f"{tmp}/src/a.c"])[2] is False
        assert build.include_dirs(sources + resources) == sorted(
            {str(tmp / "src"), str(tmp / "src/sub dir")}
        )
        raises(build.BuildError, build.expand_sources, [f"{tmp}/nope/*.c"])
        raises(build.BuildError, build.expand_sources, [f"{tmp}/src/*.txt"])
        raises(build.BuildError, build.expand_sources, [f"{tmp}/src/*.rc"])


def test_pascal_inputs():
    pascal.check_inputs(["x86-ansi", "amd64-unicode"])
    raises(pascal.BuildError, pascal.check_inputs, ["arm64-unicode"])
    raises(pascal.BuildError, pascal.check_inputs, [])
    with tempfile.TemporaryDirectory() as tmp:
        for f in ("Hello.dpr", "nsis.pas", "Hello.res"):
            (Path(tmp) / f).write_text("")
        assert pascal.find_project(f"{tmp}/Hello.dpr").name == "Hello.dpr"
        raises(pascal.BuildError, pascal.find_project, f"{tmp}/Hello.res")
        raises(pascal.BuildError, pascal.find_project, f"{tmp}/Hello.lpr")


def test_rust_inputs():
    rust.check_inputs(["x86-ansi", "arm64-unicode"])
    raises(rust.BuildError, rust.check_inputs, ["x64-ansi"])
    raises(rust.BuildError, rust.check_inputs, [])
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "Cargo.toml").write_text("")
        (Path(tmp) / "lib.rs").write_text("")
        assert rust.find_manifest(f"{tmp}/Cargo.toml").name == "Cargo.toml"
        raises(rust.BuildError, rust.find_manifest, f"{tmp}/lib.rs")
        raises(rust.BuildError, rust.find_manifest, f"{tmp}/sub/Cargo.toml")


def test_cargo_command():
    ansi = rust.cargo_command("Cargo.toml", "x86-ansi", "obj")
    assert ansi[ansi.index("--target") + 1] == "i686-pc-windows-msvc"
    assert ansi[-3:] == ["--no-default-features", "--features", "ansi"]
    unicode = rust.cargo_command("Cargo.toml", "arm64-unicode", "obj")
    assert unicode[unicode.index("--target") + 1] == "aarch64-pc-windows-msvc"
    assert "--features" not in unicode


def test_find_dll():
    manifest = Path("/p/Cargo.toml")

    def artifact(manifest_path, kind, *filenames):
        return {
            "reason": "compiler-artifact",
            "manifest_path": str(manifest_path),
            "target": {"kind": kind},
            "filenames": list(filenames),
        }

    messages = [
        {"reason": "build-script-executed"},
        artifact("/dep/Cargo.toml", ["cdylib"], "/t/dep.dll"),
        artifact(manifest, ["cdylib", "rlib"], "/t/hello.dll", "/t/hello.dll.lib"),
    ]
    assert rust.find_dll(messages, manifest) == Path("/t/hello.dll")
    raises(rust.BuildError, rust.find_dll, messages[:2], manifest)
    raises(
        rust.BuildError,
        rust.find_dll,
        [artifact(manifest, ["rlib"], "/t/libhello.rlib")],
        manifest,
    )


def test_toolchain_inputs():
    check = toolchain.check_inputs
    check("msvc", "a/*.c", "", "Windows")
    check("mingw", "a/*.c", "", "Linux")
    check("fpc", "", "a/Hello.dpr")
    check("rust", "", "a/Cargo.toml")
    raises(common.BuildError, check, "gcc", "a/*.c", "")
    raises(common.BuildError, check, "msvc", "a/*.c", "", "Linux")
    raises(common.BuildError, check, "mingw", "a/*.c", "", "Windows")
    raises(common.BuildError, check, "rust", "a/*.rs", "a/Cargo.toml")
    raises(common.BuildError, check, "msvc", "a/*.c", "a/Hello.dpr")
    raises(common.BuildError, check, "msvc", "", "a/Hello.dpr")
    raises(common.BuildError, check, "fpc", "a/Hello.dpr", "")
    raises(common.BuildError, check, "msvc", "", "")


def test_step_outputs():
    """action.yml reads steps.vars.outputs.X; between them the resolves write exactly those."""
    read = set(
        re.findall(r"steps\.vars\.outputs\.([\w-]+)", (ROOT / "action.yml").read_text())
    )
    with tempfile.TemporaryDirectory() as tmp:
        outputs = Path(tmp) / "outputs"
        for name, entry in toolchain.TOOLCHAINS.items():
            environ = {
                "TOOLCHAIN": name,
                "RUNNER_OS": entry.runner_os,
                "RUNNER_TEMP": tmp,
                "OUTPUT_DIR": tmp,
                "GITHUB_OUTPUT": str(outputs),
                entry.takes.upper(): "Contrib/Hello/hello" + min(entry.exts),
            }
            with mock.patch.dict(os.environ, environ, clear=True):
                toolchain.main("resolve")
        written = {line.split("=", 1)[0] for line in outputs.read_text().splitlines()}
    assert written == read, f"written {sorted(written)}, read {sorted(read)}"


def test_rss_md5():
    # Shape of https://sourceforge.net/projects/nsis/rss?path=/NSIS%203/3.12
    rss = (
        "<item><link>https://sourceforge.net/projects/nsis/files/NSIS%203/3.12/nsis-3.12.zip/download</link>"
        '<media:content url="https://sourceforge.net/projects/nsis/files/NSIS 3/3.12/nsis-3.12.zip/download" filesize="1">'
        '<media:hash algo="md5">00000000000000000000000000000000</media:hash></media:content></item>'
        '<item><media:content url="https://sourceforge.net/projects/nsis/files/NSIS 3/3.12/nsis-3.12-src.tar.bz2/download" filesize="1818389">'
        '<media:hash algo="md5">8ec7c3e1228ac4eb96e5e421610b4aae</media:hash></media:content></item>'
    )
    assert (
        common.rss_md5(rss, "nsis-3.12-src.tar.bz2")
        == "8ec7c3e1228ac4eb96e5e421610b4aae"
    )
    raises(common.BuildError, common.rss_md5, rss, "nsis-3.11-src.tar.bz2")


def test_pe_machine():
    header = bytearray(128)
    struct.pack_into("<I", header, 60, 64)
    header[64:68] = b"PE\0\0"
    struct.pack_into("<H", header, 68, 0x8664)
    assert common.pe_machine(bytes(header)) == 0x8664
    raises(common.BuildError, common.pe_machine, bytes(128))

    with tempfile.TemporaryDirectory() as tmp:
        dll = Path(tmp) / "Hello.dll"
        dll.write_bytes(bytes(header))
        common.verify_dll(dll, 0x8664)
        raises(common.BuildError, common.verify_dll, dll, 0x014C)
        raises(common.BuildError, common.verify_dll, Path(tmp) / "missing.dll", 0x8664)


def test_resolve_version():
    sha = "0123456789abcdef"
    assert archive.resolve_version("tag", "v1.2.3", sha) == "1.2.3"
    assert archive.resolve_version("tag", "1.0", sha) == "1.0"
    assert archive.resolve_version("branch", "12/merge", sha) == "0123456"
    raises(common.BuildError, archive.resolve_version, "branch", "main", "")


def test_stage():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for f in (
            "plugins/Plugins/x86-unicode/Hello.dll",
            "repo/LICENSE",
            "repo/README.md",
            "repo/readme.txt",
            "repo/LICENSE-MIT",
            "repo/Contrib/Hello/hello.c",
            "repo/Docs/Hello/manual.md",
            "repo/Examples/Hello/hello.nsi",
            "repo/Include/Hello.nsh",
        ):
            (tmp / f).parent.mkdir(parents=True, exist_ok=True)
            (tmp / f).write_text("")
        (tmp / "repo/README").mkdir()

        tree = tmp / "tree"
        archive.stage(tree, tmp / "plugins", tmp / "repo")
        files = sorted(
            p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file()
        )
        assert files == [
            "Docs/Hello/manual.md",
            "Examples/Hello/hello.nsi",
            "Include/Hello.nsh",
            "LICENSE",
            "Plugins/x86-unicode/Hello.dll",
            "README.md",
            "readme.txt",
        ], files
        # Contrib/ is the source, it is not shipped
        assert not (tree / "Contrib").exists()
        assert installer.find_license(tree) == tree / "LICENSE"

        (tree / "LICENSE").unlink()
        raises(common.BuildError, installer.find_license, tree)
        # Without a top-level one, the shallowest under Docs/ is shown
        for f in ("Docs/Hello/doc/license.rtf", "Docs/Hello/COPYING"):
            (tree / f).parent.mkdir(parents=True, exist_ok=True)
            (tree / f).write_text("")
        assert installer.find_license(tree) == tree / "Docs/Hello/COPYING"
        (tree / "UNLICENSE").write_text("")
        assert installer.find_license(tree) == tree / "UNLICENSE"
        raises(common.BuildError, archive.stage, tree, tmp / "repo", tmp / "repo")


def test_check_layout():
    def layout(*files):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for f in files:
                (tmp / f).parent.mkdir(parents=True, exist_ok=True)
                (tmp / f).write_text("")
            errors, suggestions = check_layout.check(tmp, "Hello")
        return [p for p, _ in errors], [p for p, _ in suggestions]

    good = (
        "LICENSE.md",
        "Contrib/Hello/src/hello.c",
        "Docs/Hello/x.md",
        "Include/Hello.nsh",
    )
    assert layout(*good) == ([], ["README.md"])
    assert layout(*good, "readme.txt", ".git/x.dll") == ([], [])
    assert layout("Contrib/Hello/Hello.dpr")[0] == ["LICENSE"]
    assert layout("Contrib/Hello/Hello.dpr", "Docs/Hello/License.txt")[0] == []
    assert layout("LICENSE", "Contrib/hello/hello.c")[0] == ["Contrib/Hello"]
    assert layout("LICENSE", "Contrib/Hello/notes.txt")[0] == ["Contrib/Hello"]
    assert layout("LICENSE", "contrib/Hello/a.c")[0] == ["contrib", "Contrib/Hello"]
    assert layout(*good, "Plugins/x86-ansi/Hello.dll")[0] == [
        "Plugins",
        "Plugins/x86-ansi/Hello.dll",
    ]
    assert layout(*good, "Examples/hello.nsi", "Docs/Other/x.md")[0] == [
        "Docs/Other",
        "Examples/hello.nsi",
    ]
    # Separate from Docs/, which a case-insensitive file system would merge it into
    assert layout("LICENSE", "Contrib/Hello/a.c", "docs/x.md")[0] == ["docs"]


if __name__ == "__main__":
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok {test.__name__}")
    print(f"{len(tests)} passed")
    sys.exit(0)
