import pytest


@pytest.fixture
def runner_args():
    return ["--count", "3"]
