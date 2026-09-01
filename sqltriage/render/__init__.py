"""Turn a Report into text, JSON, Markdown or a single line."""
from .jsonout import to_dict, to_json
from .markdown import to_markdown
from .oneline import to_oneline
from .text import to_text

FORMATS = {"text": to_text, "json": to_json, "markdown": to_markdown, "oneline": to_oneline}

__all__ = ["FORMATS", "to_dict", "to_json", "to_markdown", "to_oneline", "to_text"]
