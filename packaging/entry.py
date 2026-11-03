"""Script PyInstaller freezes. The package's own __main__ uses relative imports, so it cannot be the entry point."""
from klotse.__main__ import main

if __name__ == "__main__":
    main()
