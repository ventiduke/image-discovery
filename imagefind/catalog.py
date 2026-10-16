"""Flatten the catalog JSON into one entry per item that has an ID or label.

Every section (``weapons``, ``weaponSkins``, ...) is a list of objects with an
``id``. Some hold further items: a skin's ``chromas`` and ``levels``, a
character's ``abilities``. Abilities have no ``id``, only a ``label`` of the
form ``<character id>_Ability1``, so entries are keyed by ID when there is one
and by label otherwise, and images can match either.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ENGLISH = "en-US"


@dataclass
class Entry:
    key: str  # upper-cased id, or label when there is no id
    id: str | None
    label: str | None
    section: str  # top-level catalog key, e.g. "weaponSkins"
    kind: str  # e.g. "weaponSkins", "weaponSkins.chromas", "characters.abilities"
    parent: str | None  # key of the containing entry
    name: str
    description: str
    names: dict[str, str] = field(default_factory=dict)  # other languages
    fields: dict[str, object] = field(default_factory=dict)  # simple values: category, weaponId, ...
    raw: dict = field(default_factory=dict)  # the JSON object, minus nested entries


@dataclass
class Catalog:
    version: str | None
    entries: dict[str, Entry]
    lookup: dict[str, str]  # upper-cased id or label -> entry key

    def find(self, name: str) -> Entry | None:
        key = self.lookup.get(name.upper())
        return self.entries[key] if key else None


class CatalogError(ValueError):
    pass


def load(path: Path) -> Catalog:
    try:
        with path.open("rb") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise CatalogError(f"Can't read {path.name}: {exc}") from None
    if not isinstance(data, dict):
        raise CatalogError(f"{path.name} isn't a catalog (expected a JSON object).")
    return parse(data)


def parse(data: dict) -> Catalog:
    entries: dict[str, Entry] = {}
    lookup: dict[str, str] = {}
    for section, items in data.items():
        if isinstance(items, list):
            for item in items:
                _walk(item, section, section, None, entries, lookup)
    version = data.get("version")
    return Catalog(version=version if isinstance(version, str) else None, entries=entries, lookup=lookup)


def is_localized(value: object) -> bool:
    return isinstance(value, dict) and "defaultText" in value and "localizedByCulture" in value


def english(value: object) -> str:
    """The English text of a localized value."""
    if not is_localized(value):
        return ""
    by_culture = value.get("localizedByCulture") or {}
    return (value.get("defaultText") or by_culture.get(ENGLISH) or "").strip()


def _is_entry(value: object) -> bool:
    return isinstance(value, dict) and (
        isinstance(value.get("id"), str) or isinstance(value.get("label"), str)
    )


def _walk(obj, section, kind, parent, entries, lookup) -> None:
    if not _is_entry(obj):
        return
    ident = obj.get("id") if isinstance(obj.get("id"), str) else None
    label = obj.get("label") if isinstance(obj.get("label"), str) else None
    key = (ident or label).upper()
    if key in entries:  # the same item listed twice: keep the first
        return

    raw: dict = {}
    fields: dict[str, object] = {}
    children: list[tuple[str, object]] = []
    for name, value in obj.items():
        nested = _nested_entries(value)
        if nested:
            children.extend((f"{kind}.{name}", child) for child in nested)
            continue
        raw[name] = value
        if name in ("id", "label", "name", "description"):
            continue
        if isinstance(value, (str, int, float, bool)) and value != "":
            fields[name] = value
        elif is_localized(value) and english(value):
            fields[name] = english(value)

    names = {}
    if is_localized(obj.get("name")):
        english_name = english(obj["name"])
        for culture, text in (obj["name"].get("localizedByCulture") or {}).items():
            if text and culture != ENGLISH and text != english_name:
                names[culture] = text
    entry = Entry(
        key=key,
        id=ident,
        label=label,
        section=section,
        kind=kind,
        parent=parent,
        name=english(obj.get("name")),
        description=english(obj.get("description")),
        names=names,
        fields=fields,
        raw=raw,
    )
    entries[key] = entry
    for alias in (ident, label):
        if alias:
            lookup.setdefault(alias.upper(), key)
    for child_kind, child in children:
        _walk(child, section, child_kind, key, entries, lookup)


def _nested_entries(value: object) -> list:
    """Items held inside an entry: a list of entries, or a dict of them (abilities)."""
    if isinstance(value, list):
        items = value
    elif isinstance(value, dict) and not is_localized(value):
        items = list(value.values())
    else:
        return []
    return items if items and all(_is_entry(item) for item in items) else []
