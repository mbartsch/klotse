# Packaging Klotse

Native packages for Windows and macOS, built with [PyInstaller](https://pyinstaller.org). One spec file
(`klotse.spec`) serves both platforms; the logo in `src/klotse/assets/klotse_logo.png` is the single source for the
`.ico` and `.icns` icons.

| Platform | Output (in `dist/`) | Built by |
| --- | --- | --- |
| Windows | `Klotse-<version>-windows-x64-setup.exe` (installer), `Klotse-<version>-windows-x64-portable.zip` | `packaging/windows/build.ps1` |
| macOS | `Klotse-<version>-macos-<arm64\|x86_64>.dmg` containing `Klotse.app` | `packaging/macos/build.sh` |

Everything builds in an isolated `.venv-build` that holds only the runtime dependencies plus PyInstaller and Pillow
(`requirements-build.txt`), so nothing from your development environment leaks into the app.

## Building locally

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

The zip is always made. The installer also needs [Inno Setup 6](https://jrsoftware.org/isinfo.php)
(`winget install JRSoftware.InnoSetup` or `choco install innosetup`); without it the script says so and stops after the zip.

macOS:

```sh
packaging/macos/build.sh
```

Build on each kind of Mac you want to support: an Apple Silicon Mac makes `arm64`, an Intel Mac makes `x86_64`.

Both scripts finish by running the packaged app's own self-test (below), and fail if it does not pass.

## Building in CI

`.github/workflows/package.yml` builds Windows and both macOS architectures on GitHub's runners:

- **Actions → Package → Run workflow** builds everything and keeps the files as downloadable artifacts.
- **Pushing a tag** such as `v0.1.0` does the same and then creates a **draft** release with the files and a
  `SHA256SUMS.txt` attached. Nothing is public until you publish the draft. The tag must equal
  `klotse.__version__`, or the build stops.

To release: change `__version__` in `src/klotse/__init__.py`, merge, then `git tag vX.Y.Z && git push origin vX.Y.Z`.

## Linux and PyPI (pipx)

Linux is served by the Python package: `pipx install klotse` (or `pipx install git+https://github.com/mbartsch/klotse`
until it is on PyPI). It needs no packaging beyond `pyproject.toml`; AppImage, `.deb`, `.rpm`, Flatpak and Snap were
considered and left out for now (see the note below). pipx installs a command but no launcher, so
`klotse --install-desktop-entry` writes `~/.local/share/applications/klotse.desktop` and the icon (no root needed), and
`klotse --uninstall-desktop-entry` removes them.

Build and check the Python package locally (the build environment has `build`, `twine` and `trove-classifiers`):

```sh
python -m build --outdir dist/pypi
python -m twine check dist/pypi/*
```

`.github/workflows/pypi.yml` does this in CI, installs the wheel with pipx on Python 3.10, 3.12 and 3.13, runs the
self-test headless and inside a real X11 session (Xvfb), and checks the desktop entry.

### Publishing to PyPI

Nothing has been published. A published version can never be replaced, only yanked, so check the CI results first.
The workflow publishes with PyPI's *trusted publishing* (a short-lived token issued to the workflow, nothing stored in
GitHub secrets). One-time setup:

1. On pypi.org (account needed), add a **pending publisher** for the project name `klotse`: owner `mbartsch`,
   repository `klotse`, workflow `pypi.yml`, environment `pypi`. This also reserves the name when the first upload
   happens; the name was free when this was written, and nothing holds it until then.
2. On GitHub, create the environments `pypi` (and `testpypi` for dry runs) under Settings → Environments, and add
   yourself as a **required reviewer** of `pypi`, so every upload waits for your approval.
3. Manual runs: Actions → PyPI → Run workflow uploads to PyPI by default (still waiting for your approval in the
   `pypi` environment). Untick "Upload to PyPI" to only build and test; tick "Also upload to TestPyPI" for a dry
   run there (needs the same pending publisher on test.pypi.org, environment `testpypi`). Pull requests never upload.
4. Release: change `__version__`, merge, `git tag vX.Y.Z && git push origin vX.Y.Z`. The tag must equal
   `klotse.__version__`. After the build and smoke tests pass, approve the `pypi` deployment.

Why not AppImage, `.deb` or `.rpm` yet: they are good fits (not sandboxed, so `kubectl`, cloud auth helpers and the
host terminal keep working) and are the likely next step if pipx proves too much work for users. Flatpak and Snap are
poor fits because their sandboxes block exactly those host tools.

## What a packaged app does differently

These are the reasons the packaged app has code paths that a plain `python -m klotse` run does not:

- **Self-test.** `Klotse --self-test [REPORT_FILE]` checks that the Qt plugins, the logo and the imports are really
  inside the bundle and that the dialogs can be built. The Windows app has no console, so give it a file to write to.
  `Klotse --version` prints the version.
- **macOS shell sessions.** The Terminal/iTerm2 runner is normally a separate Python script. A packaged app has no
  script file and no separate Python, so the shell is started with `Klotse --terminal-session ...`, handled in
  `klotse/cli.py` before the GUI starts.
- **macOS `PATH`.** Apps started from Finder or the Dock get only `/usr/bin:/bin:/usr/sbin:/sbin`, so `kubectl`
  (Homebrew) and kubeconfig helpers (`aws`, `gcloud`, `kubelogin`) would not be found. At startup
  `klotse/ui/environment.py` adds the usual directories and asks the user's login shell for its `PATH`.
- **`NSAppleEventsUsageDescription`** (in the spec) and the `apple-events` entitlement are what let macOS show the
  "Klotse wants to control Terminal" prompt instead of silently refusing.
- **`kubectl` is not bundled.** Drain, Shell and Attach need it on `PATH`; the self-test reports whether it was found.

## Signing and notarization (not set up yet)

Without signing, the packages work but the operating systems warn:

- **Windows:** SmartScreen shows "Windows protected your PC". Users choose **More info → Run anyway**. Removing the
  warning needs an Authenticode certificate (or Azure Trusted Signing) and a `signtool` step; none is configured.
- **macOS:** the app is ad-hoc signed (PyInstaller does this, and Apple Silicon requires it), which is enough to run it
  on the Mac that built it. On other Macs Gatekeeper blocks it until the user right-clicks the app and chooses **Open**,
  or runs `xattr -dr com.apple.quarantine /Applications/Klotse.app`.

`build.sh` already supports proper signing and notarization when these environment variables are set:

| Variable | Meaning |
| --- | --- |
| `MACOS_CODESIGN_IDENTITY` | `Developer ID Application: Name (TEAMID)`; needs a paid Apple Developer account |
| `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` | credentials for `xcrun notarytool` (an app-specific password) |

Using them in CI additionally needs the certificate imported into a temporary keychain on the runner; that step is
not written yet, because it cannot be tested without the certificate.

## Not done yet

- Windows installer: compiled and tested with a silent per-user install, the installed app's self-test and a clean
  uninstall (folder, Start Menu entry and uninstall registration all removed). Not tested: the interactive wizard,
  the "all users" mode (needs administrator rights), upgrading over an older version, and SmartScreen behaviour.
- macOS: the CI builds (Apple Silicon and Intel) pass: the app builds, its self-test passes inside `Klotse.app`, the
  ad-hoc signature verifies and the DMG is created. Not tested: running the app on a real Mac, the Terminal/iTerm2
  permission prompt and shell sessions, the Finder `PATH` fix, Gatekeeper behaviour, and opening the DMG.
- Linux has no native package (AppImage, `.deb`, `.rpm`); pipx/PyPI is the supported route. Nothing is on PyPI yet.
- No automatic update check, no Windows ARM64 build (the x64 app runs under emulation).
- No CI exists yet for the test suite itself.
