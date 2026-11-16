import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Final

_TELEGRAM_DIR: Final[Path] = Path(__file__).parents[2] / "app" / "telegram"
_SEND_METHODS: Final[frozenset[str]] = frozenset(
    {"answer", "send_message", "answer_photo", "edit_text", "edit_caption", "reply"}
)
_TEXT_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"text", "caption", "error_message", "message_text", "title", "description"}
)
_TRANSLATOR_FACTORIES: Final[frozenset[str]] = frozenset(
    {"get_translator", "get_translator_by_locale"}
)


def _is_translation(node: ast.expr) -> bool:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
    ):
        return False
    owner = node.func.value
    if isinstance(owner, ast.Name):
        return owner.id == "runner"
    return (
        isinstance(owner, ast.Call)
        and isinstance(owner.func, (ast.Name, ast.Attribute))
        and (owner.func.id if isinstance(owner.func, ast.Name) else owner.func.attr)
        in _TRANSLATOR_FACTORIES
    )


def _raw_sends(tree: ast.AST) -> Iterator[int]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        is_send = isinstance(node.func, ast.Attribute) and node.func.attr in _SEND_METHODS
        values = [*node.args] if is_send else []
        values += [kw.value for kw in node.keywords if kw.arg in _TEXT_KEYWORDS]
        if any(_is_translation(value) for value in values):
            yield node.lineno


def test_translations_never_reach_telegram_raw() -> None:
    # `[🚫](emoji:id)` stays literal unless the text goes through render_window_text,
    # plain_to_html (+ parse_mode HTML), premium_emoji_glyphs (alerts) or answer_notice
    offenders = [
        f"{path.relative_to(_TELEGRAM_DIR.parents[1])}:{line}"
        for path in sorted(_TELEGRAM_DIR.rglob("*.py"))
        for line in _raw_sends(ast.parse(path.read_text()))
    ]
    assert offenders == []
