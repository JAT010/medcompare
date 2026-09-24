"""Run the DEMO Hospital API server.

Launches the fully-simulated hospital API on ``127.0.0.1:8001`` so the mock
source adapter has an HTTP surface to consume:

    python -m hospital_extractor.mockapi        # or: medcompare-demo-server

Every payload served is fictional; the service is never to be deployed
against, or claimed to be, a real hospital.
"""

from __future__ import annotations

import uvicorn

from hospital_extractor.mockapi.app import create_mock_app

__all__ = ["main"]

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8001


def main() -> None:
    uvicorn.run(create_mock_app(), host=_DEFAULT_HOST, port=_DEFAULT_PORT, log_level="info")


if __name__ == "__main__":
    main()