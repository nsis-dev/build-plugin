"""Helpers every action script shares: inputs, outputs, the Plugin API and DLL checks.

Not run directly; imported by the build, archive, installer and layout scripts.
"""

import hashlib
import io
import os
import re
import struct
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

# Bumped deliberately, so rebuilding a plugin tag yields the same Plugin API
PINNED_NSIS_VERSION = "3.12"

# target: (arch, unicode, PE machine)
TARGETS = {
    "x86-ansi": ("x86", False, 0x014C),
    "x86-unicode": ("x86", True, 0x014C),
    "amd64-unicode": ("amd64", True, 0x8664),
    "arm64-unicode": ("arm64", True, 0xAA64),
}
API_FILES = (
    "Contrib/ExDLL/pluginapi.c",
    "Contrib/ExDLL/pluginapi.h",
    "Contrib/ExDLL/nsis_tchar.h",
    "Contrib/ExDLL/nsis.pas",
    "Source/exehead/api.h",
)


class BuildError(Exception):
    pass


def env(name, default=""):
    return os.environ.get(name, "").strip() or default


def split(value):
    """Comma or newline separated list; spaces stay part of an item."""
    return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]


def set_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def is_doc(path, stems=("LICENSE", "LICENCE", "README")):
    """LICENSE, License.md, readme.txt and the like."""
    return path.is_file() and path.name.split(".")[0].upper() in stems


def rss_md5(rss, filename):
    """SourceForge's file feed lists each file's MD5."""
    m = re.search(
        rf'/{re.escape(filename)}/download"[^>]*><media:hash algo="md5">([0-9a-f]{{32}})',
        rss,
    )
    if not m:
        raise BuildError(f"SourceForge lists no MD5 for {filename}")
    return m.group(1)


def download(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "nsis-dev/build-plugin"}
    )
    for attempt in range(5):
        print(f"Downloading {url}", flush=True)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except OSError as e:
            if attempt == 4:
                raise BuildError(f"download failed: {url}: {e}")
            time.sleep(2**attempt)


def fetch_plugin_api(version, plugin_api):
    """Stages API_FILES flat in <plugin_api>/nsis, the layout NSIS installs the headers in."""
    target = Path(plugin_api) / "nsis"
    if all((target / Path(f).name).is_file() for f in API_FILES):
        return

    folder = f"NSIS%20{version.split('.')[0]}/{version}"
    filename = f"nsis-{version}-src.tar.bz2"
    md5 = rss_md5(
        download(f"https://sourceforge.net/projects/nsis/rss?path=/{folder}").decode(
            "utf-8", "replace"
        ),
        filename,
    )
    data = download(
        f"https://downloads.sourceforge.net/project/nsis/{folder}/{filename}"
    )
    if hashlib.md5(data).hexdigest() != md5:
        raise BuildError(f"{filename} does not match SourceForge's MD5 {md5}")

    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tar:
        for f in API_FILES:
            member = tar.extractfile(f"nsis-{version}-src/{f}")
            if member is None:
                raise BuildError(f"{filename} has no {f}")
            (target / Path(f).name).write_bytes(member.read())


def pe_machine(data):
    offset = struct.unpack_from("<I", data, 60)[0]
    if data[offset : offset + 4] != b"PE\0\0":
        raise BuildError("not a PE file")
    return struct.unpack_from("<H", data, offset + 4)[0]


def verify_dll(dll, machine):
    """Every Toolchain checks its output the same way: it exists and is built for the target."""
    if not dll.is_file():
        raise BuildError(f"{dll} was not produced")
    got = pe_machine(dll.read_bytes())
    if got != machine:
        raise BuildError(f"{dll} has PE machine {got:#06x}, expected {machine:#06x}")


def run(cmd, **kwargs):
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    result = subprocess.run([str(c) for c in cmd], check=False, **kwargs)
    if result.returncode != 0:
        raise BuildError(f"{Path(str(cmd[0])).name} failed")
    return result


def guard(fn):
    """Turns a BuildError into a GitHub error annotation and a failed step."""
    try:
        fn()
    except BuildError as e:
        print(f"::error::{e}", flush=True)
        sys.exit(1)


def cli(**commands):
    """Every build script is called as <script>.py <command> by action.yml."""
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(f"usage: {sys.argv[0]} {'|'.join(commands)}")
    guard(commands[sys.argv[1]])
