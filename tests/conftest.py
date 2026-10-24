import os
import tempfile

import pytest

os.environ.setdefault("RESPECT_ROBOTS", "false")
os.environ.setdefault("RATE_LIMIT_DELAY", "0")


@pytest.fixture()
def tmp_db(tmp_path):
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture()
def tmp_snapshots():
    with tempfile.TemporaryDirectory() as d:
        yield d
