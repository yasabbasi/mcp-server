import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from mcp import Client

from mcp_server.cli import registered
from mcp_server.outputs import SUPPORTED

SAMPLES = Path(__file__).resolve().parent / "samples"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=SUPPORTED)
def version(request: pytest.FixtureRequest) -> str:
    """Each supported contract version; a test parametrizes `version` to read only some."""
    found: str = request.param
    return found


@pytest.fixture(autouse=True)
def samples(version: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point DATA_OUTPUT_DIR at the copied samples of the version, which every test reads."""
    folder = SAMPLES / version
    monkeypatch.setenv("DATA_OUTPUT_DIR", str(folder))
    return folder


@pytest.fixture
def outputs(samples: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point DATA_OUTPUT_DIR at a copy of the samples that a test may alter."""
    copy = tmp_path / "output"
    shutil.copytree(samples, copy, ignore=shutil.ignore_patterns("SOURCE.json"))
    monkeypatch.setenv("DATA_OUTPUT_DIR", str(copy))
    return copy


@pytest.fixture
async def client() -> AsyncIterator[Client]:
    """Connect to the server in process; tool errors come back as `is_error` results."""
    async with Client(registered()) as connected:
        yield connected
