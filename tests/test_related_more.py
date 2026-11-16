"""A capped Related group keeps the entries it hides, its '... and N more' note is a link, and the link shows the whole group."""
from klotse.k8s import related as rel
from klotse.ui import related_view
from klotse.k8s.related import Relation


def test_the_more_note_keeps_the_hidden_entries(monkeypatch):
    monkeypatch.setattr(rel, "SHOWN", 3)
    entries = [Relation("Owns", "Pod", "shop", f"p{i}") for i in range(7)]
    capped = rel._cap(entries)
    note = capped[-1]
    assert note.note == "more" and note.name == "… and 4 more"
    assert [e.name for e in note.more] == ["p3", "p4", "p5", "p6"]


def test_the_note_is_a_link_that_is_not_shown_in_full_at_first():
    note = Relation("Owns", "Pod", "", "… and 4 more", 0, "more", (Relation("Owns", "Pod", "shop", "p3"),))
    page = related_view.render([Relation("Owns", "Pod", "shop", "p0"), note])
    assert "p3" not in page
    assert related_view.more_link("Owns", "Pod", 0) in page and "(show all)" in page


def test_expanding_the_group_shows_every_entry_with_its_links():
    note = Relation("Owns", "Pod", "", "… and 2 more", 0, "more", (Relation("Owns", "Pod", "shop", "p3"), Relation("Owns", "Pod", "shop", "p4")))
    page = related_view.render([Relation("Owns", "Pod", "shop", "p0"), note], expanded=frozenset({("Owns", "Pod", 0)}))
    assert "p3" in page and "p4" in page and related_view.link("Pod", "shop", "p4") in page
    assert "show fewer" in page


def test_the_more_link_round_trips_and_other_links_are_refused():
    href = related_view.more_link("Owns", "Pod", 2)
    assert related_view.parse_more(href) == ("Owns", "Pod", 2)
    assert related_view.parse_more(related_view.link("Pod", "shop", "p0")) is None
    assert related_view.parse_more("klotse-more:not-json") is None
