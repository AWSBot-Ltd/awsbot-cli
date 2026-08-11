from __future__ import annotations

import uuid


def make_symbol_id(
    workspace_id: str,
    git_ref: str,
    file_path: str,
    qualname: str,
    kind: str,
    start_line: int,
    end_line: int,
) -> str:
    key = f"{workspace_id}|{git_ref}|{file_path}|{qualname}|{kind}|{start_line}|{end_line}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
