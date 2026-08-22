# The engine builds on your machines, not GitHub's

`engine-release.yml` is the only workflow that cannot run on a hosted runner.
The build **recompiles the game**: `CMakeLists.txt` needs `game/default.xex`,
`game/data/webkit/EAWebkit.xex` and the Title Update 3 package before the
`generate-all` target will produce a line of C++. That dump is yours and does
not go to GitHub, so the job goes to the dump instead.

Do this once per machine.

## 1. Register the runner

Settings → Actions → Runners → New self-hosted runner, then follow the commands
it gives you. Two things it does not tell you:

**Labels.** Add `skate3-engine` plus the platform, so the matrix in
`engine-release.yml` can address exactly one machine:

| machine | labels |
|---|---|
| Linux | `self-hosted, skate3-engine, Linux, X64` |
| Windows | `self-hosted, skate3-engine, Windows, X64` |
| macOS | `self-hosted, skate3-engine, macOS, ARM64` |

**A dedicated user.** This repository is public. Run the service as a user that
is not you, with a work directory nowhere near `~/Documents/skate3`:

```bash
sudo ./svc.sh install runner-svc && sudo ./svc.sh start   # Linux
./svc.sh install && ./svc.sh start                        # macOS (launchd)
./config.cmd                                              # Windows: answer yes to "run as service"
```

## 2. Point it at your game files

In the runner's own `.env` file (next to `run.sh`), **not** in repository
secrets — a secret is readable by any workflow that gets merged, the runner
environment only by a job that has already started on that machine:

```
SKATE3_GAME_DATA_ROOT=/path/to/game
SKATE3_TITLE_UPDATE_PACKAGE=/path/to/TU3
CCACHE_DIR=/path/to/ccache
VULKAN_SDK=/path/to/VulkanSDK/macOS   # macOS only
```

## 3. Toolchain

All three want CMake 3.25+, Ninja and Clang 18+. ReXGlue requires Clang, so
MSVC and Apple Clang will not do.

**Linux.** `clang-20`, `lld-20`, `ninja-build`, `glslc`, `libvulkan-dev`, the
SDL3 X11 build dependencies, `ccache`. Preset `linux-release`.

If the machine is newer than Ubuntu 22.04, build in a container. glibc is
forward- but not backward-compatible, so an engine linked against 2.43 runs on
Ubuntu 26.04 and nothing older:

```bash
docker run --rm -v "$PWD:/src" -v /path/to/game:/game:ro -w /src ubuntu:22.04 ...
```

**Windows.** The engine is MSVC-ABI (`CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded`,
and it links `comdlg32 winhttp d3dcompiler gdiplus`), so this is **not** the
MSYS2 stack the launcher is built with. Install VS 2022 Build Tools 17.10+ with
the Windows 11 SDK, Ninja, CMake and LLVM 20 for `clang-cl`, and enter the
environment with `vcvars64.bat` from a `cmd` step rather than a third-party
action.

> **`CMakePresets.json` has no Windows preset.** There is `base`, `linux-base`
> and `macos-base` and nothing else, so `windows-base` / `windows-release` have
> to be added to the fork before this job can exist at all.

**macOS (Apple Silicon).** Xcode Command Line Tools, `brew install cmake ninja
llvm ccache` — the preset hardcodes `/opt/homebrew/opt/llvm`, the unversioned
formula — and the LunarG Vulkan SDK with `VULKAN_SDK` exported.
`rexglue_helpers.cmake` reads it to stage `libMoltenVK.dylib` and write
`MoltenVK_icd.json`, and it supplies the `glslc` that the FidelityFX step needs.
Configure time needs network access: FidelityFX is fetched from git.

## 4. Two things the workflow relies on

- **`clean: false` on checkout, and a build directory outside the workspace.**
  Checkout's default `git clean -ffdx` would delete the incremental ninja tree,
  and a cold build regenerates 289 MB of C++ across 2265 edges.
- **The upload is an allowlist.** The build stages `default.xexp` and
  `EAWebkit.xexp` next to the binary; archiving the build directory wholesale
  would publish retail files from a public repository. There is an assertion in
  the job that fails the build if any `*.xex*` reaches the archive — leave it
  there.

## 5. Cutting a release

```bash
gh release create v0.1.0 --draft --notes-file NOTES.md
gh workflow run engine-release.yml -f tag=v0.1.0 -f platforms=linux
gh workflow run engine-release.yml -f tag=v0.1.0 -f platforms=macos
git push origin v0.1.0     # builds the three launchers, checksums, publishes
```

The draft comes first on purpose. Engine builds take tens of minutes and need
the machine awake; a `needs:` between them and the launcher would mean a sleeping
Mac fails the whole run and throws away three good launcher builds with it.
