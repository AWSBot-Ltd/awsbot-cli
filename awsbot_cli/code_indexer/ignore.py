from __future__ import annotations

from pathlib import Path
import pathspec


def load_ignore_spec(repo_root: Path) -> pathspec.PathSpec:
    patterns: list[str] = []

    gitignore = repo_root / ".gitignore"
    if gitignore.exists():
        patterns += gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()

    extra = repo_root / ".codeindexignore"
    if extra.exists():
        patterns += extra.read_text(encoding="utf-8", errors="ignore").splitlines()

    # Always ignore MCP's own index dir if it lives under repo
    patterns += [".mcp_index/", ".mcp_index/**"]

    patterns = [p for p in patterns if p.strip() and not p.strip().startswith("#")]
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def should_ignore(rel_posix: str, spec: pathspec.PathSpec) -> bool:
    # Pathspec expects forward-slash paths
    return spec.match_file(rel_posix)
