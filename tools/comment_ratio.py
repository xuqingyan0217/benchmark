from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import tokenize


@dataclass(frozen=True)
class CommentRatio:
    """注释率统计结果，供测试和人工检查共用同一口径。"""

    source_lines: int
    comment_lines: int

    @property
    def ratio(self) -> float:
        """没有源码行时按达标处理，避免空目录产生除零错误。"""
        if self.source_lines == 0:
            return 1.0
        return self.comment_lines / self.source_lines


def calculate_comment_ratio(root: str | Path) -> CommentRatio:
    """统计目录下 Python 文件的整体注释率。

    统计口径和 OpenSpec 保持一致：
    - 分母是非空源码行。
    - 分子是 `#` 注释行和文档字符串行。
    - 多行文档字符串按实际覆盖行数计入。
    """
    root_path = Path(root)
    source_lines = 0
    comment_lines: set[tuple[Path, int]] = set()

    for path in sorted(root_path.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        source_lines += sum(1 for line in lines if line.strip())
        comment_lines.update((path, line_no) for line_no in _hash_comment_lines(path))
        comment_lines.update((path, line_no) for line_no in _docstring_lines(text))

    return CommentRatio(source_lines=source_lines, comment_lines=len(comment_lines))


def _hash_comment_lines(path: Path) -> set[int]:
    """用 tokenize 识别真正的 `#` 注释，避免误算字符串里的井号。"""
    result: set[int] = set()
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type == tokenize.COMMENT:
                result.add(token.start[0])
    return result


def _docstring_lines(text: str) -> set[int]:
    """收集模块、类、函数文档字符串覆盖的行号。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()

    result: set[int] = set()
    nodes: list[ast.AST] = [tree]
    nodes.extend(node for node in ast.walk(tree) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)))
    for node in nodes:
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            start = getattr(first, "lineno", None)
            end = getattr(first, "end_lineno", start)
            if start is not None and end is not None:
                result.update(range(start, end + 1))
    return result


if __name__ == "__main__":
    ratio = calculate_comment_ratio("vllm_bench_platform")
    print(f"{ratio.comment_lines}/{ratio.source_lines} = {ratio.ratio:.2%}")
