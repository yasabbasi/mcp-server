"""Copy a contract version's samples and contract doc from data-pipeline's tag, or check them."""

import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "tests" / "samples"
REPO = "yasabbasi/data-pipeline"
CONTRACT_DOC = "docs/contract.md"
COPIED = r"samples/v\d+/(parquet/\w+\.parquet|quality_report\.json)|docs/contract\.md"


def git(repo: Path, *args: str) -> bytes:
    command = ["git", "-C", str(repo), *args]
    return subprocess.run(command, check=True, stdout=subprocess.PIPE).stdout


@contextmanager
def clone_of(pipeline: str) -> Iterator[Path]:
    """Yield a clone of the pipeline: the local path, or a temporary bare clone of a URL."""
    if Path(pipeline).is_dir():
        yield Path(pipeline)
        return
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(["git", "clone", "--quiet", "--bare", pipeline, folder], check=True)
        yield Path(folder)


def tag_commit(repo: Path, tag: str) -> str:
    """Return the commit a tag points at."""
    return git(repo, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").decode().strip()


def local_path(name: str) -> Path:
    """Return where this repo keeps a pipeline file: samples under tests/, the doc as it is."""
    return ROOT / "tests" / name if name.startswith("samples/") else ROOT / name


def copied_names(repo: Path, commit: str, path: str) -> list[str]:
    """Return the files a version's copy holds: its samples and the contract doc at a commit."""
    listed = git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", path, CONTRACT_DOC)
    return sorted(name for name in listed.decode().split("\0") if re.fullmatch(COPIED, name))


def sync(repo: Path, version: str) -> None:
    """Copy one version's files from its contract tag and record where they came from."""
    tag, path = f"contract-{version}", f"samples/{version}"
    commit = tag_commit(repo, tag)
    names = copied_names(repo, commit, path)
    if names == [CONTRACT_DOC] or CONTRACT_DOC not in names:
        raise SystemExit(f"{tag} does not have both {path}/ and {CONTRACT_DOC}")
    shutil.rmtree(SAMPLES / version, ignore_errors=True)
    digests = {}
    for name in names:
        content = git(repo, "cat-file", "blob", f"{commit}:{name}")
        local_path(name).parent.mkdir(parents=True, exist_ok=True)
        local_path(name).write_bytes(content)
        digests[name] = hashlib.sha256(content).hexdigest()
    record = {"repo": REPO, "tag": tag, "commit": commit, "path": path, "sha256": digests}
    text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    (SAMPLES / version / "SOURCE.json").write_text(text)
    print(f"copied {len(names)} files from {REPO} at {tag} ({commit[:12]})")


def problems(repo: Path, source: Path, newest: bool) -> list[str]:
    """Return how one version's copies differ from its contract tag; the doc is the newest's."""
    record = json.loads(source.read_text())
    tag = f"contract-{source.parent.name}"
    commit = tag_commit(repo, tag)
    if record["tag"] != tag or record["commit"] != commit:
        return [f"{tag} is at {commit}; {source.relative_to(ROOT)} records {record['commit']}"]
    if sorted(record["sha256"]) != copied_names(repo, commit, record["path"]):
        return [f"{source.relative_to(ROOT)} does not record the files of {tag}"]
    return [
        f"{local_path(name).relative_to(ROOT)} differs from {name} at {tag}"
        for name in record["sha256"]
        if (newest or name != CONTRACT_DOC)
        and (
            not local_path(name).is_file()
            or local_path(name).read_bytes() != git(repo, "cat-file", "blob", f"{commit}:{name}")
        )
    ]


def check(repo: Path) -> int:
    """Print how the copied versions differ from their contract tags; return 1 if any does."""
    sources = sorted(SAMPLES.glob("v*/SOURCE.json"), key=lambda s: int(s.parent.name[1:]))
    found = [p for source in sources for p in problems(repo, source, source == sources[-1])]
    if not sources:
        found = [f"no SOURCE.json under {SAMPLES.relative_to(ROOT)}/"]
    for problem in found:
        print(problem)
    if not found:
        versions = ", ".join(source.parent.name for source in sources)
        print(f"the copied samples match their contract tags: {versions}")
    return 1 if found else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pipeline", help="a local clone of data-pipeline, or its git URL")
    parser.add_argument("version", nargs="?", help="the contract version to copy, such as v1")
    parser.add_argument("--check", action="store_true", help="compare every copy with its tag")
    args = parser.parse_args()
    if args.check == bool(args.version):
        parser.error("give a version to copy, or --check without one")
    if args.version and not re.fullmatch(r"v\d+", args.version):
        parser.error("a version looks like v1")
    try:
        with clone_of(args.pipeline) as repo:
            if args.check:
                return check(repo)
            sync(repo, args.version)
    except subprocess.CalledProcessError as error:
        print(f"failed: {shlex.join(error.cmd)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
