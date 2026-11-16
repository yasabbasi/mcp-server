import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_claude_md_is_at_most_40_lines() -> None:
    assert len((ROOT / "CLAUDE.md").read_text().splitlines()) <= 40


def test_workflows_pin_actions_by_commit() -> None:
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        for used in re.findall(r"uses: (\S+)", workflow.read_text()):
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", used), used


def test_readme_only_names_make_targets_that_exist() -> None:
    targets = set(re.findall(r"^([a-z-]+):", (ROOT / "Makefile").read_text(), flags=re.M))
    named = set(re.findall(r"`make ([a-z-]+)", (ROOT / "README.md").read_text()))
    assert named <= targets
