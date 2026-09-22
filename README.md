# build-plugin

![License](https://img.shields.io/github/license/nsis-dev/build-plugin?color=blue&style=for-the-badge)
![Release](https://img.shields.io/github/v/release/nsis-dev/build-plugin?style=for-the-badge)
![CI](https://img.shields.io/github/actions/workflow/status/nsis-dev/build-plugin/ci.yml?style=for-the-badge)

> [!CAUTION]
> This GitHub Action is an early proof-of-concept, use at your own risk!

Build [NSIS](https://nsis.sourceforge.io/) plugins from source and release them from GitHub.

## Usage

Build on every push. When a release is published, attach these files to it:

- a Release Archive
- a Plugin Installer
- checksums and build attestations

```yaml
on:
  push:
  pull_request:
  release:
    types: [published]

jobs:
  plugin:
    runs-on: windows-latest
    permissions:
      contents: write
      id-token: write
      attestations: write
    steps:
      - uses: actions/checkout@v7
      - uses: nsis-dev/build-plugin@v1
        with:
          name: Hello
          sources: Contrib/Hello/*.c
          targets: x86-ansi,x86-unicode,amd64-unicode
```

A release of tag `v1.0.0` gets these assets:

| Asset                   | Contents                                                                                |
| ----------------------- | --------------------------------------------------------------------------------------- |
| `Hello-1.0.0.zip`       | `Plugins/<target>/Hello.dll`, your `Docs/`, `Examples/` and `Include/`, LICENSE, README |
| `Hello-1.0.0-setup.exe` | Installs those folders into NSISDIR, showing the LICENSE on its license page            |
| `SHA256SUMS`            | Checksums of the above                                                                  |

Other builds upload the zip and installer as the workflow artifact `<name>-<toolchain>`, versioned by the short commit SHA.
Check where a released file came from with `gh attestation verify Hello-1.0.0.zip --repo <owner>/<repo>`.
Attestations are free on public repositories; on a private one they need GitHub Team or Enterprise, so set `attestations: false` and drop the `id-token` and `attestations` permissions there.

To also build with a second Toolchain as a check, use a matrix and release only one of them:

```yaml
jobs:
  plugin:
    strategy:
      matrix:
        include:
          - { toolchain: msvc, os: windows-latest, release: true }
          - { toolchain: mingw, os: ubuntu-latest, release: false }
    runs-on: ${{ matrix.os }}
    permissions:
      contents: write
      id-token: write
      attestations: write
    steps:
      - uses: actions/checkout@v7
      - uses: nsis-dev/build-plugin@v1
        with:
          name: Hello
          sources: Contrib/Hello/*.c
          toolchain: ${{ matrix.toolchain }}
          release: ${{ matrix.release }}
```

## Layout

A Plugin repository is laid out like NSISDIR. The action checks this first and fails before building if it isn't:

| Path                | Rule                                                       |
| ------------------- | ---------------------------------------------------------- |
| `Contrib/<name>/`   | **Required**, holds the source, not shipped                   |
| `LICENSE`           | **Required** at the top level, any extension, or `LICENCE`    |
| `README`            | Suggested at the top level, any extension                     |
| `Docs/<name>/`      | Optional, nothing else in `Docs/`, ships as-is                |
| `Examples/<name>/`  | Optional, nothing else in `Examples/`, ships as-is            |
| `Include/`          | Optional, ships as-is                                         |
| `Plugins/`, `*.dll` | Not committed, the action builds them                         |

`Contrib`, `Docs`, `Examples`, `Include` and `Plugins` must be spelled that way.

## Inputs

| Name        | Default                     | Description                                                                                                   |
| ----------- | --------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `name`      |                             | Plugin name, i.e. the DLL basename scripts call as `Name::Func`.                                              |
| `sources`   |                             | `msvc` and `mingw` only: C/C++ source globs (`.c`, `.cpp`, `.cxx`, `.cc`, `.rc`).                             |
| `project`   |                             | `fpc` and `rust` only: path of the Pascal project file (`.dpr`, `.lpr`, `.pas`) or the plugin's `Cargo.toml`. |
| `targets`   | `x86-unicode,amd64-unicode` | Any of `x86-ansi`, `x86-unicode`, `amd64-unicode`, `arm64-unicode`. `x86-ansi` is opt-in.                     |
| `toolchain` | `msvc`                      | `msvc`, `fpc` or `rust` on a Windows runner, `mingw` on a Linux runner.                                       |
| `crt`       | `static`                    | C/C++ only: `static` links the C runtime in; `none` builds without it, entry point `DllMain`.                 |
| `release`   | `true`                      | Attach the files to the release that triggered the run.                                                       |
| `attestations` | `true`                   | Attest build provenance. Free on public repositories; a private one needs GitHub Team or Enterprise.          |

Each Toolchain takes exactly one of `sources` and `project`; the action fails before building if the other one is set.
List inputs are comma or newline separated, so paths may contain spaces.

There are deliberately no inputs for defines, libraries, include directories or raw flags:

- Every directory holding a matched source is an include directory.
- A fixed list of common Windows import libraries is linked; unused ones add no imports. If a Plugin needs one that's missing, open an issue.
- Put defines in a header.
- The NSIS version of the Plugin API and the Free Pascal version are pinned, and only change in a build-plugin release.

## Outputs

| Name          | Description                                         |
| ------------- | --------------------------------------------------- |
| `plugins-dir` | Directory containing `Plugins/<target>/<name>.dll`. |
| `archive`     | Path of the Release Archive.                        |
| `installer`   | Path of the Plugin Installer.                       |

## Writing the plugin

### C/C++

Include the Plugin API the way NSIS installs it:

```c
#include <windows.h>
#include <nsis/pluginapi.h>
```

`pluginapi.c` is compiled in. `UNICODE` and `_UNICODE` are defined for `*-unicode` targets. A C++ plugin needs `extern "C"` on its exported functions.

> [!NOTE]
> `arm64-unicode` builds with `msvc` only; Ubuntu's MinGW has no arm64 compiler.
> Official NSIS releases only ship x86 stubs, so amd64 and arm64 plugins need a
> self-built makensis to be used.

### Pascal

Use the unit NSIS ships:

```pascal
library Hello;

uses
  nsis, Windows;

procedure Greet(const hwndParent: HWND; const string_size: integer; const variables: NSISPTChar; const stacktop: pointer); cdecl;
begin
  Init(hwndParent, string_size, variables, stacktop);
  PushString('Hello, ' + PopString() + '!');
end;

exports
  Greet;

begin
end.
```

Code compiles in Delphi mode (`-Mdelphi`) unless the source sets `{$mode}`. `UNICODE` is defined for `*-unicode` targets, which switches `nsis.pas` to wide strings. The official Free Pascal installer is downloaded from SourceForge, checked against the MD5 listed there, and cached.

> [!NOTE]
> Free Pascal is not Delphi. Plugins that use the VCL (`Graphics`), Delphi-only
> units such as `WinSvc`, or assembler calling into Delphi's RTL need porting.
> Free Pascal 3.2 has no arm64 Windows target.

### Rust

Keep the crate in `Contrib/<name>/`, or put a workspace `Cargo.toml` at the top level with `Contrib/<name>` as a member, and point `project` at the Plugin's own `Cargo.toml`. The action builds it with Cargo for the `*-pc-windows-msvc` targets and relies on two things only:

- `[lib] crate-type = ["cdylib"]`. The DLL is renamed to `<name>.dll`, so the crate name doesn't have to match.
- `*-unicode` targets build the crate's default features. `x86-ansi` builds with `--no-default-features --features ansi`, so a crate that builds `x86-ansi` needs an `ansi` feature that switches it to ANSI strings.

Export functions as `#[unsafe(no_mangle)] pub extern "C" fn`. No Plugin API is supplied; bring a crate for it or write the stack handling yourself. A `rust-toolchain.toml` next to the manifest picks the Rust version, otherwise the runner's stable Rust is used. Size settings such as `opt-level`, `lto` and `panic = "abort"` belong in the crate's `[profile.release]`.

## Development

[mise](https://mise.jdx.dev/) installs the tools (Python, ruff, actionlint, hk) and the
[hk](https://hk.jdx.dev/) pre-commit hook:

```sh
mise install
mise run check   # lint, formatting, script self-check
mise run fix     # apply lint and formatting fixes
```

`action.yml` runs stdlib-only Python scripts from `scripts/`, which also run locally:

```sh
NAME=HelloC SOURCES='test/hello/Contrib/HelloC/*.c' TOOLCHAIN=mingw python3 scripts/build_c.py build
```

## License

[Apache License, Version 2.0](LICENSE)
