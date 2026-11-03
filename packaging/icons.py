"""Make the platform icon files from the one source logo, so assets/klotse_logo.png stays the single source."""
from pathlib import Path

from PIL import Image

ICO_SIZES = [(size, size) for size in (16, 24, 32, 48, 64, 128, 256)]


def _load(png):
    return Image.open(png).convert("RGBA")


def make_ico(png, out):
    """Windows icon with all the sizes Explorer, the taskbar and the title bar ask for."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _load(png).save(out, format="ICO", sizes=ICO_SIZES)
    return out


def make_icns(png, out):
    """macOS icon (Pillow writes the standard 16 to 512 px sizes, including @2x)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _load(png).save(out, format="ICNS")
    return out


if __name__ == "__main__":
    import sys
    source, ico = sys.argv[1], sys.argv[2]
    print(make_ico(source, ico))
