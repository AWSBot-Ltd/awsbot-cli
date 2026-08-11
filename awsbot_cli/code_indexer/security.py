from __future__ import annotations

from pathlib import Path
from typing import Iterable


class PathAccessError(ValueError):
    pass


def normalize_repo_root(repo_root: str, allowed_roots: Iterable[Path]) -> Path:
    rr = Path(repo_root).expanduser().resolve()
    if not rr.exists() or not rr.is_dir():
        raise PathAccessError(f"repo_root does not exist or is not a directory: {rr}")

    for base in allowed_roots:
        try:
            rr.relative_to(base)
            return rr
        except ValueError:
            continue

    raise PathAccessError(
        "repo_root is not within MCP_ALLOWED_ROOTS. "
        f"repo_root={rr} allowed={', '.join(str(p) for p in allowed_roots)}"
    )


def normalize_rel_file(repo_root: Path, file_path: str) -> Path:
    # Force relative paths; block absolute paths and traversal.
    p = Path(file_path)
    if p.is_absolute():
        raise PathAccessError("file_path must be relative to repo_root (not absolute).")
    resolved = (repo_root / p).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as e:
        raise PathAccessError(
            "file_path escapes repo_root (.. or symlink traversal)."
        ) from e
    return resolved
