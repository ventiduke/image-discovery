"""Tags: the words in item names, so items that share a word can be grouped.

"Reaver Vandal" gives the tags ``reaver`` and ``vandal``. Tags are lower-case;
each also keeps the spelling it's most often written with, for display.
Numbers, single letters and a few filler words are left out.
"""

from __future__ import annotations

import re
from collections import Counter

_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
STOPWORDS = frozenset(
    "a an and at by de del des der die el en for from in la le les of on or the to vs with x".split()
)


def words(name: str) -> list[str]:
    """The tag-worthy words in a name, as written (original case), without repeats."""
    out, seen = [], set()
    for word in _WORD.findall(name):
        word = re.sub(r"['’]s$", "", word, flags=re.IGNORECASE)  # "Viper's" -> "Viper"
        key = word.casefold()
        if len(key) < 2 or key.isdigit() or key in STOPWORDS or key in seen:
            continue
        seen.add(key)
        out.append(word)
    return out


def tag_names(names: list[str]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Tags for each name, and the display spelling of each tag."""
    spellings: dict[str, Counter] = {}
    per_name: dict[str, list[str]] = {}
    for name in names:
        tags = []
        for word in words(name):
            key = word.casefold()
            spellings.setdefault(key, Counter())[word] += 1
            tags.append(key)
        per_name[name] = tags
    display = {key: counter.most_common(1)[0][0] for key, counter in spellings.items()}
    return per_name, display
