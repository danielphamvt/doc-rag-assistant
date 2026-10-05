"""Sanity tests for UI template assets (pure file reads, no app imports)."""
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent.parent / "ui"

REQUIRED_TEMPLATES = [
    "spinner.html",
    "reasoning.html",
    "header.html",
    "footer.html",
    "chatbot_placeholder.html",
    "architecture.html",
    "plus_menu.html",
]

REQUIRED_ASSETS = [
    "static/css/style.css",
    "static/css/architecture.css",
    "static/js/focus.js",
    "static/js/mermaid.min.js",
]


def test_required_templates_and_assets_exist():
    for name in REQUIRED_TEMPLATES:
        assert (UI_DIR / "templates" / name).is_file(), f"missing template: {name}"
    for name in REQUIRED_ASSETS:
        assert (UI_DIR / name).is_file(), f"missing asset: {name}"


def test_spinner_template_formats_cleanly():
    src = (UI_DIR / "templates" / "spinner.html").read_text(encoding="utf-8")
    rendered = src.format(status="Đang xử lý...")
    assert "Đang xử lý..." in rendered
    assert "{" not in rendered.split("Đang xử lý")[0].replace("{{", "")


def test_reasoning_template_formats_cleanly():
    src = (UI_DIR / "templates" / "reasoning.html").read_text(encoding="utf-8")
    rendered = src.format(open_attr="open", reasoning_content="Bước suy luận mẫu")
    assert "Bước suy luận mẫu" in rendered
    assert "<details" in rendered


def test_focus_js_has_balanced_braces():
    src = (UI_DIR / "static" / "js" / "focus.js").read_text(encoding="utf-8")
    # Regression guard: an unbalanced script silently kills all autoload JS.
    assert src.count("{") == src.count("}"), "focus.js has unbalanced braces"
