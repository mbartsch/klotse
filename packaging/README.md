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
- Everything macOS (`build.sh`, the spec's macOS branch, entitlements, the DMG) is untested until CI runs it.
- No automatic update check, no Linux package, no Windows ARM64 build (the x64 app runs under emulation).
- No CI exists yet for the test suite itself.
