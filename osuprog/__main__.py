"""Usage : python -m osuprog sync [pseudo]"""
import sys

from .sync import sync


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # console Windows : accents et ★
    args = sys.argv[1:]
    if not args or args[0] != "sync":
        print(__doc__)
        sys.exit(1)
    sync(args[1] if len(args) > 1 else None)


if __name__ == "__main__":
    main()
