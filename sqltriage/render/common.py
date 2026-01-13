"""Pieces the renderers share."""


def fmt_time(value):
    return value.strftime("%Y-%m-%d %H:%M:%S")


def describe_changes(counts):
    """'2 new, 1 worse, 3 unchanged' from the baseline counts."""
    words = [("new", "new"), ("worse", "worse"), ("better", "better"), ("same", "unchanged")]
    return ", ".join("{} {}".format(counts[key], word) for key, word in words if counts.get(key))  or "nothing to compare"
