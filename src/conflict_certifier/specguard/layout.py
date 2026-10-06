"""SpecGuard-only artifact layout; legacy flat outputs remain readable."""
from pathlib import Path


def relative_path(name: str) -> Path:
    if name in {"task.txt", "inputs.json", "result.json", "cert.lean"}:
        return Path(name)
    if name in {"spec.lean", "tests.lean", "connector.lean"}:
        return Path("leans") / name
    if name.endswith("_log.json"):
        return Path("trajs") / name
    if name in {"selected_source.json", "selected_tests.json", "test_coverage.json"}:
        return Path("intermediates/inputs") / name
    if name in {"config.json", "verification.json", "connector_prompt.txt"}:
        return Path("config") / name
    return Path("intermediates") / name


def artifact_path(root: Path, name: str) -> Path:
    path = root / relative_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def saved_path(root: Path, name: str) -> Path:
    path = root / relative_path(name)
    return path if path.exists() else root / name
