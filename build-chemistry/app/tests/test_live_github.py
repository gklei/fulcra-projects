import os

import pytest

from build_chemistry import project_public_stars


@pytest.mark.live_github
@pytest.mark.skipif(os.getenv("RUN_LIVE_GITHUB") != "1", reason="set RUN_LIVE_GITHUB=1 for live API check")
def test_real_public_account_produces_bounded_valid_projection() -> None:
    projection = project_public_stars("sindresorhus")
    assert 1 <= len(projection.records) <= 100
    assert all(record.recorded_at.tzinfo is not None for record in projection.records)