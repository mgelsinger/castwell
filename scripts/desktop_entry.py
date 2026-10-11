"""PyInstaller entry point for the windowed Castwell desktop application."""
from castwell.desktop import main


if __name__ == "__main__":
    raise SystemExit(main())
