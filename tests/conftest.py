"""Pytest configuration.

All tests under this directory are offline unit tests — no broker
credentials, no network, no sleeps. The previous `collect_ignore`
list of vendor samples and removed-strategy-era tests was emptied
when those files were deleted; see the post-LIVE-01 audit-cleanup
commit for the trail.
"""

import logging

import pytest


@pytest.fixture(autouse=True)
def _suppress_file_logging():
    """Remove FileHandlers from the root logger so test runs don't write to
    the production logs/ic_system_YYYYMMDD.log file."""
    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    for h in file_handlers:
        root.removeHandler(h)
    yield
