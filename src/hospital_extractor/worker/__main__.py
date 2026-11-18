"""Allow ``python -m hospital_extractor.worker``."""

import sys

from hospital_extractor.worker.cli import main

if __name__ == "__main__":
    sys.exit(main())
