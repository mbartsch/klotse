#!/usr/bin/env bash
# Build the macOS app and a DMG:  packaging/macos/build.sh
#
# Output: dist/Klotse.app and dist/Klotse-<version>-macos-<arch>.dmg   (arch is arm64 or x86_64: build on each kind of Mac)
#
# Optional environment variables:
#   PYTHON                    interpreter used to create .venv-build (default: python3)
#   MACOS_CODESIGN_IDENTITY   "Developer ID Application: Name (TEAMID)". Without it the app is only ad-hoc signed:
#                             it runs on this Mac, but Gatekeeper warns on other Macs.
#   APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD
#                             with an identity, also notarize the DMG (xcrun notarytool) and staple the ticket.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
ROOT="$PWD"

VENV=.venv-build
if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating build environment in $VENV ..."
  "${PYTHON:-python3}" -m venv "$VENV"
fi
PY="$VENV/bin/python"
"$PY" -m pip install --disable-pip-version-check -q -r requirements-build.txt

export PYTHONPATH="$ROOT/src"
# The version is the one written in src/klotse/_version.py (changed only by a release PR); the app reports it with +g<commit>
# added when this commit is not exactly the tagged release (written to the git-ignored src/klotse/_build_info.py)
"$PY" scripts/release.py write-build-info
VERSION="$("$PY" -c 'import klotse; print(klotse.__version__)')"
ARCH="$(uname -m)"
echo "Building Klotse $VERSION for $ARCH"

rm -rf dist/Klotse dist/Klotse.app
"$PY" -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging/klotse.spec
APP="dist/Klotse.app"
[ -d "$APP" ] || { echo "PyInstaller did not produce $APP" >&2; exit 1; }

echo "Self-test of the packaged app ..."
"$APP/Contents/MacOS/Klotse" --self-test

echo "Verifying the signature ..."
codesign --verify --deep --strict --verbose=2 "$APP"
if [ -z "${MACOS_CODESIGN_IDENTITY:-}" ]; then
  echo "NOTE: ad-hoc signature only (no MACOS_CODESIGN_IDENTITY). Other Macs show a Gatekeeper warning." >&2
fi

DMG="dist/Klotse-$VERSION-macos-$ARCH.dmg"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
rm -f "$DMG"
hdiutil create -volname "Klotse $VERSION" -srcfolder "$STAGING" -ov -format UDZO "$DMG"

if [ -n "${MACOS_CODESIGN_IDENTITY:-}" ]; then
  codesign --force --timestamp --sign "$MACOS_CODESIGN_IDENTITY" "$DMG"
  if [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ]; then
    echo "Notarizing (this can take a few minutes) ..."
    xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_PASSWORD" --wait
    xcrun stapler staple "$DMG"
  else
    echo "NOTE: signed but not notarized (APPLE_ID, APPLE_TEAM_ID and APPLE_APP_PASSWORD not all set)." >&2
  fi
fi
echo "Created $DMG"
