"""``python -m efp_app`` is the same as the ``efp-app`` command."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
