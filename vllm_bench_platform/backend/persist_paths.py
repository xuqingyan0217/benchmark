from __future__ import annotations

from pathlib import Path, PurePosixPath


def run_host_path(persist_root: str | Path, namespace: str, run_id: str) -> str:
    return str(PurePosixPath(str(persist_root).replace("\\", "/")) / namespace / run_id)


def results_query_root(persist_root: str | Path, namespace: str) -> Path:
    return Path(persist_root) / namespace
