import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from mcp_server.outputs import SUPPORTED

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "tests" / "samples"
VERSIONS = sorted(source.parent.name for source in SAMPLES.glob("v*/SOURCE.json"))
CONTRACT_DOC = "docs/contract.md"
NEWEST = max(VERSIONS, key=lambda version: int(version[1:]))


def record(version: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((SAMPLES / version / "SOURCE.json").read_text())
    return loaded


def local_path(name: str) -> Path:
    """Where this repo keeps a pipeline file: samples under tests/, the contract doc as it is."""
    return ROOT / "tests" / name if name.startswith("samples/") else ROOT / name


def test_every_supported_contract_version_has_copied_samples() -> None:
    assert sorted(SUPPORTED) == VERSIONS


@pytest.mark.parametrize("version", VERSIONS)
def test_the_record_names_the_pipeline_tag_and_commit_it_was_copied_from(version: str) -> None:
    source = record(version)
    assert source["repo"] == "yasabbasi/data-pipeline"
    assert (source["tag"], source["path"]) == (f"contract-{version}", f"samples/{version}")
    assert re.fullmatch(r"[0-9a-f]{40}", source["commit"])


@pytest.mark.parametrize("version", VERSIONS)
def test_copied_files_match_their_recorded_checksums(version: str) -> None:
    """These files are copies: `make sync-samples` writes them. The doc is the newest version's."""
    recorded = record(version)["sha256"]
    if version != NEWEST:
        del recorded[CONTRACT_DOC]
    actual = {name: hashlib.sha256(local_path(name).read_bytes()).hexdigest() for name in recorded}
    assert actual == recorded


@pytest.mark.parametrize("version", VERSIONS)
def test_every_sample_file_and_the_contract_doc_are_recorded(version: str) -> None:
    folder = SAMPLES / version
    files = {p for p in folder.rglob("*") if p.is_file() and p.name != "SOURCE.json"}
    assert {local_path(name) for name in record(version)["sha256"]} == files | {
        ROOT / "docs" / "contract.md"
    }
