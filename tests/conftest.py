"""Pytest configuration.

All tests under this directory are offline unit tests — no broker
credentials, no network, no sleeps. The previous `collect_ignore`
list of vendor samples and removed-strategy-era tests was emptied
when those files were deleted; see the post-LIVE-01 audit-cleanup
commit for the trail.
"""
