import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Final

_BACKEND_DIR: Final[Path] = Path(__file__).parents[1]


def _annotations(tree: ast.AST) -> Iterator[ast.expr]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
                if arg is not None and arg.annotation is not None:
                    yield arg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def test_annotations_use_optional_and_union_not_pep604() -> None:
    offenders = [
        f"{path.relative_to(_BACKEND_DIR)}:{annotation.lineno}"
        for path in sorted(_BACKEND_DIR.rglob("*.py"))
        if not any(part.startswith(".") for part in path.relative_to(_BACKEND_DIR).parts)
        for annotation in _annotations(ast.parse(path.read_text()))
        if any(
            isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr)
            for sub in ast.walk(annotation)
        )
    ]
    assert offenders == [], "use typing.Optional[X] / typing.Union[X, Y]"
