from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Chunk:
    file_path: str  # posix relative path
    language: str
    chunk_type: str  # function/class/module/window
    symbol_name: str | None
    start_line: int
    end_line: int
    text: str


def guess_language(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".md": "markdown",
        ".json": "json",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".toml": "toml",
        ".sh": "bash",
        ".sql": "sql",
    }.get(ext, "text")


def chunk_python(
    rel_path: str, text: str, max_chunk_chars: int
) -> tuple[list[Chunk], list[tuple[str, str, int, int]]]:
    # returns: chunks + symbols rows (symbol_name, kind, start, end)
    chunks: list[Chunk] = []
    symbols: list[tuple[str, str, int, int]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return (
            chunk_fallback(rel_path, text, "python", max_lines=220, overlap_lines=40),
            symbols,
        )

    lines = text.splitlines()
    # module-level chunk (top of file) - keeps imports + global constants context
    head_end = min(len(lines), 80)
    head_text = "\n".join(lines[:head_end]).strip()
    if head_text:
        chunks.append(
            Chunk(
                rel_path,
                "python",
                "module",
                None,
                1,
                head_end,
                head_text[:max_chunk_chars],
            )
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = getattr(node, "name", None)
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if start is None or end is None:
                continue
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            symbols.append((name or "<unknown>", kind, int(start), int(end)))
            seg_lines = lines[start - 1 : end]
            seg_text = "\n".join(seg_lines).strip()
            if not seg_text:
                continue
            # If too big, split by windows
            if len(seg_text) > max_chunk_chars:
                chunks.extend(
                    _window_chunks(
                        rel_path,
                        "python",
                        kind,
                        name,
                        seg_lines,
                        start,
                        max_chunk_chars,
                    )
                )
            else:
                chunks.append(
                    Chunk(
                        rel_path, "python", kind, name, int(start), int(end), seg_text
                    )
                )
    return chunks, symbols


def _window_chunks(
    rel_path: str,
    lang: str,
    chunk_type: str,
    symbol: str | None,
    lines: list[str],
    start_line: int,
    max_chunk_chars: int,
) -> list[Chunk]:
    out: list[Chunk] = []
    buf: list[str] = []
    buf_start = start_line
    cur_line = start_line

    def flush(buf_start: int, cur_line_exclusive: int, buf: list[str]) -> None:
        t = "\n".join(buf).strip()
        if t:
            out.append(
                Chunk(
                    rel_path,
                    lang,
                    "window",
                    symbol,
                    buf_start,
                    cur_line_exclusive - 1,
                    t,
                )
            )

    for line in lines:
        # if adding line would exceed max_chunk_chars, flush
        prospective = "\n".join(buf + [line])
        if buf and len(prospective) > max_chunk_chars:
            flush(buf_start, cur_line, buf)
            buf = []
            buf_start = cur_line
        buf.append(line)
        cur_line += 1

    if buf:
        flush(buf_start, cur_line, buf)
    return out


def chunk_fallback(
    rel_path: str, text: str, language: str, max_lines: int, overlap_lines: int
) -> list[Chunk]:
    lines = text.splitlines()
    out: list[Chunk] = []
    i = 0
    n = len(lines)
    while i < n:
        j = min(n, i + max_lines)
        window = "\n".join(lines[i:j]).strip()
        if window:
            out.append(
                Chunk(
                    file_path=rel_path,
                    language=language,
                    chunk_type="window",
                    symbol_name=None,
                    start_line=i + 1,
                    end_line=j,
                    text=window,
                )
            )
        if j >= n:
            break
        i = max(0, j - overlap_lines)
    return out
