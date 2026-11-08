"""Command palette matching (pure, no Qt): a flat list of things you can jump to or do, ranked against what was typed.

An entry is `Entry(category, label, detail, run)`; `run()` does it. Typing narrows the list: every word must match the label, the
category or the detail as a fuzzy sequence (letters in order, not necessarily next to each other: `hc` finds "Health check"), and
better matches rank first: whole-word and word-start hits, letters that are next to each other, shorter labels."""
from dataclasses import dataclass
from typing import Callable

CATEGORY_ORDER = ("Go to", "Namespace", "Action", "Cluster", "Search")


@dataclass
class Entry:
    category: str
    label: str
    detail: str = ""
    run: Callable = None


def fuzzy_score(word, text):
    """How well `word` matches `text` (higher is better), or None when its letters do not appear in order."""
    word, text = word.lower(), text.lower()
    if not word:
        return 0
    position, score, previous, start_of_word = 0, 0, -2, True
    for char in word:
        found = text.find(char, position)
        if found < 0:
            return None
        starts_word = found == 0 or not text[found - 1].isalnum()
        score += 10 if starts_word else 1
        if found == previous + 1:
            score += 6  # next to the previous letter
        previous, position = found, found + 1
    index = text.find(word)
    if index >= 0:
        score += 30 + (15 if index == 0 or not text[index - 1].isalnum() else 0)  # the word itself appears
    return score


def score_entry(entry, query):
    """The rank of an entry for a query (None = does not match). Each word must match the label, category or detail."""
    total = 0
    for word in query.split():
        best = None
        for weight, text in ((3, entry.label), (1, entry.category), (1, entry.detail)):
            value = fuzzy_score(word, text)
            if value is not None:
                value = value * weight - (len(entry.label) / 10 if weight == 3 else 0)
                best = value if best is None else max(best, value)
        if best is None:
            return None
        total += best
    return total


def rank(entries, query, limit=60):
    """The entries that match `query`, best first (all of them in category order when the query is empty), at most `limit`."""
    query = query.strip()
    order = {name: i for i, name in enumerate(CATEGORY_ORDER)}
    if not query:
        return sorted(entries, key=lambda e: (order.get(e.category, 99), e.label))[:limit]
    scored = [(score_entry(e, query), e) for e in entries]
    scored = [(s, e) for s, e in scored if s is not None]
    return [e for _s, e in sorted(scored, key=lambda p: (-p[0], order.get(p[1].category, 99), p[1].label))][:limit]
