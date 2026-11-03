# PyInstaller spec for Klotse, used for both Windows and macOS.
# Run from the repository root:  python -m PyInstaller --noconfirm --clean packaging/klotse.spec
# (packaging/windows/build.ps1 and packaging/macos/build.sh do this, and the rest of the release steps.)
#
# Windows: dist/Klotse/Klotse.exe (a folder; the installer and the portable zip are made from it)
# macOS:   dist/Klotse.app
#
# Optional environment variables (macOS signing, see packaging/README.md):
#   MACOS_CODESIGN_IDENTITY   e.g. "Developer ID Application: Name (TEAMID)"; without it the app is ad-hoc signed
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821  (SPECPATH is provided by PyInstaller)
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "packaging"))

import icons  # noqa: E402
import klotse  # noqa: E402

VERSION = klotse.__version__
ASSETS = ROOT / "src" / "klotse" / "assets"
LOGO = ASSETS / "klotse_logo.png"
ICON_DIR = ROOT / "build" / "icons"
IS_WINDOWS, IS_MAC = sys.platform == "win32", sys.platform == "darwin"

# The logo files next to the package (not the editable source in assets/source/) go to klotse/assets in the app.
datas = [(str(path), "klotse/assets") for path in ASSETS.iterdir() if path.is_file()]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=[],
    excludes=["tkinter", "pytest", "_pytest", "pytestqt", "unittest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    noarchive=False,
)
# Klotse is a Qt Widgets app with no OpenGL content, so the 20 MB software OpenGL fallback is dead weight.
UNUSED_BINARIES = {"opengl32sw.dll"}
a.binaries = [entry for entry in a.binaries if os.path.basename(entry[0]).lower() not in UNUSED_BINARIES]
pyz = PYZ(a.pure)  # noqa: F821


def windows_version_file():
    """Version resource: what Explorer shows under Properties -> Details."""
    numbers = (list(int(part) for part in VERSION.split(".") if part.isdigit()) + [0, 0, 0, 0])[:4]
    path = ROOT / "build" / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = {"CompanyName": "Marcelo H. Bartsch", "FileDescription": "Klotse - Kubernetes desktop manager",
              "FileVersion": VERSION, "InternalName": "Klotse", "OriginalFilename": "Klotse.exe",
              "ProductName": "Klotse", "ProductVersion": VERSION,
              "LegalCopyright": "Copyright 2026 Marcelo H. Bartsch. Apache License 2.0."}
    strings = ", ".join(f"StringStruct('{key}', '{value}')" for key, value in fields.items())
    path.write_text(
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={tuple(numbers)}, prodvers={tuple(numbers)}, mask=0x3f, flags=0x0, "
        f"OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)), kids=[StringFileInfo([StringTable('040904B0', [{strings}])]), "
        f"VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n", encoding="utf-8")
    return str(path)


exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # one-folder build: starts faster than one-file, which unpacks itself on every launch
    name="Klotse",
    console=False,  # no console window; `Klotse.exe --self-test FILE` writes its report to a file instead
    icon=str(icons.make_ico(LOGO, ICON_DIR / "klotse.ico")) if IS_WINDOWS else (
        str(icons.make_icns(LOGO, ICON_DIR / "klotse.icns")) if IS_MAC else None),
    version=windows_version_file() if IS_WINDOWS else None,
    codesign_identity=os.environ.get("MACOS_CODESIGN_IDENTITY") or None if IS_MAC else None,
    entitlements_file=str(ROOT / "packaging" / "macos" / "entitlements.plist") if IS_MAC else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Klotse")  # noqa: F821

if IS_MAC:
    app = BUNDLE(  # noqa: F821
        coll,
        name="Klotse.app",
        icon=str(ICON_DIR / "klotse.icns"),
        bundle_identifier="io.github.mbartsch.klotse",
        version=VERSION,
        info_plist={
            "CFBundleName": "Klotse",
            "CFBundleDisplayName": "Klotse",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.developer-tools",
            # Without this macOS refuses the Terminal / iTerm2 automation that "Shell" and "Attach" rely on.
            "NSAppleEventsUsageDescription": "Klotse opens shells and attached sessions for pods and nodes in Terminal or iTerm2.",
            "NSHumanReadableCopyright": "Copyright 2026 Marcelo H. Bartsch. Apache License 2.0.",
        },
    )
