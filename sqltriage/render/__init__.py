"""Turn a Report into text, JSON or Markdown."""
from .jsonout import to_dict, to_json
from .markdown import to_markdown
from .text import to_text

FORMATS = {"text": to_text, "json": to_json, "markdown": to_markdown}

__all__ = ["FORMATS", "to_dict", "to_json", "to_markdown", "to_text"]
