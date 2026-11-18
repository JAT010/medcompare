"""Deterministic field-level change detection between payload snapshots.

Inputs are consecutive payloads of the same facility from the same source
kind. The diff is:

- recursive over JSON objects and arrays,
- order-insensitive for arrays of named objects (members matched by ``name``;
  mixed arrays fall back to index-based comparison),
- field-aware (one change row per differing leaf or per added/removed member),
- deterministic: stable ordering and values.

A member added/removed in a collection of named objects produces one change
with ``path`` set to the collection, ``entity_name`` the member label and
``change_type`` ``added``/``removed``. A scalar difference produces ``path``
as the dotted field path, ``field`` the last segment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["DetectedChange", "compute_changes"]

_MAX_VALUE_PREVIEW = 2000


@dataclass(frozen=True)
class DetectedChange:
    """One field-level difference between two payloads."""

    path: str
    change_type: str  # "added" | "removed" | "modified"
    entity_name: str = ""
    field: str = ""
    old_value: Any = None
    new_value: Any = None


def _jsonish(value: Any) -> Any:
    """Bounded JSON-safe rendering of a value for storage/display."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_VALUE_PREVIEW else value[:_MAX_VALUE_PREVIEW]
    if isinstance(value, list):
        return [_jsonish(item) for item in value[:50]]
    if isinstance(value, dict):
        return {str(key): _jsonish(val) for key, val in list(value.items())[:100]}
    return str(value)


def _scalars_equal(left: Any, right: Any) -> bool:
    """Type-strict scalar equality (``0`` is not ``False``, ``1`` is not ``"1"``)."""
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return bool(left == right)


def _label(node: Any) -> str:
    if isinstance(node, dict):
        name = node.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        for key in ("id", "code", "title"):
            value = node.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
        return "record"
    return str(node)


def _is_named_dict_list(items: list[Any]) -> bool:
    """True when every member is a dict with a non-empty string ``name``."""
    return bool(items) and all(
        isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip()
        for item in items
    )


def _has_duplicate_names(items: list[Any]) -> bool:
    """True when two members would receive the same collection identity."""
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            return True
        name = item.get("name")
        label = name.strip().casefold() if isinstance(name, str) else str(name)
        if label in seen:
            return True
        seen.add(label)
    return False


def _walk(
    previous: Any,
    current: Any,
    path: str,
    entity_name: str,
    changes: list[DetectedChange],
) -> None:
    if isinstance(previous, dict) and isinstance(current, dict):
        for key in sorted(set(previous) | set(current)):
            child_path = f"{path}.{key}" if path else str(key)
            child_entity = entity_name
            in_previous = key in previous
            in_current = key in current
            if not in_previous:
                child_entity = entity_name or str(key)
                changes.append(
                    DetectedChange(
                        path=child_path,
                        change_type="added",
                        entity_name=child_entity,
                        field=str(key),
                        old_value=None,
                        new_value=_jsonish(current[key]),
                    )
                )
            elif not in_current:
                changes.append(
                    DetectedChange(
                        path=child_path,
                        change_type="removed",
                        entity_name=entity_name,
                        field=str(key),
                        old_value=_jsonish(previous[key]),
                        new_value=None,
                    )
                )
            else:
                _walk(previous[key], current[key], child_path, child_entity, changes)
        return

    if isinstance(previous, list) and isinstance(current, list):
        if (
            _is_named_dict_list(previous)
            and _is_named_dict_list(current)
            and not _has_duplicate_names(previous)
            and not _has_duplicate_names(current)
        ):
            prev_by_name = {_label(item): item for item in previous}
            curr_by_name = {_label(item): item for item in current}
            for name in sorted(set(prev_by_name) - set(curr_by_name)):
                changes.append(
                    DetectedChange(
                        path=path,
                        change_type="removed",
                        entity_name=name,
                        old_value=_jsonish(prev_by_name[name]),
                        new_value=None,
                    )
                )
            for name in sorted(set(curr_by_name) - set(prev_by_name)):
                changes.append(
                    DetectedChange(
                        path=path,
                        change_type="added",
                        entity_name=name,
                        field="",
                        old_value=None,
                        new_value=_jsonish(curr_by_name[name]),
                    )
                )
            for name in sorted(set(prev_by_name) & set(curr_by_name)):
                _walk(
                    prev_by_name[name],
                    curr_by_name[name],
                    f"{path}.{name}" if path else name,
                    name,
                    changes,
                )
            return
        # Heterogeneous/index-based comparison.
        for index, (prev_item, curr_item) in enumerate(zip(previous, current, strict=False)):
            _walk(prev_item, curr_item, f"{path}[{index}]", entity_name, changes)
        for index in range(len(current), len(previous)):
            changes.append(
                DetectedChange(
                    path=path,
                    change_type="removed",
                    entity_name=entity_name or str(index),
                    old_value=_jsonish(previous[index]),
                    new_value=None,
                )
            )
        for index in range(len(previous), len(current)):
            changes.append(
                DetectedChange(
                    path=path,
                    change_type="added",
                    entity_name=entity_name or str(index),
                    old_value=None,
                    new_value=_jsonish(current[index]),
                )
            )
        return

    if not _scalars_equal(previous, current):
        field = path.rsplit(".", 1)[-1] if path else ""
        changes.append(
            DetectedChange(
                path=path,
                change_type="modified",
                entity_name=entity_name,
                field=field,
                old_value=_jsonish(previous),
                new_value=_jsonish(current),
            )
        )


def compute_changes(previous: Any, current: Any) -> list[DetectedChange]:
    """Return deterministic field-level changes from *previous* to *current*."""
    changes: list[DetectedChange] = []
    _walk(previous, current, "", "", changes)
    changes.sort(key=lambda change: (change.path, change.entity_name, change.change_type))
    return changes
