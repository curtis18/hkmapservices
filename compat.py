"""Small Qt 5 and Qt 6 compatibility helpers."""

from __future__ import annotations


def enum_member(owner, group: str, member: str):
    """Return a scoped enum member, falling back to the Qt 5 flat form."""
    group_type = getattr(owner, group, None)
    if group_type is not None:
        value = getattr(group_type, member, None)
        if value is not None:
            return value
    return getattr(owner, member)


def exec_dialog(dialog):
    """Call exec() on Qt 6, or exec_() on older PyQt 5 builds."""
    method = getattr(dialog, "exec", None)
    if method is None:
        method = getattr(dialog, "exec_")
    return method()
