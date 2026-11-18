"""The search criteria of find(), find_all(), matches() and the like: a tag name and, optionally, class, id, attribute
value, visible text, inner HTML and the source of the opening tag."""

from . import _attrs as at
from . import _text
from ._chars import ascii_lower, equal_fold
from ._parser import NAMES, lookup_tag

CRITERIA = ("class_", "id", "attr", "text", "id_starts_with", "text_starts_with", "body", "body_starts_with",
            "source")


class Query:
    __slots__ = ("key", "tag_name", "plain", "cls", "id", "attr_name", "attr_value", "text", "id_starts_with",
                 "text_starts_with", "body", "body_starts_with", "source")

    def __init__(self, name=None):
        if name is None:
            self.key = -1
            self.tag_name = ""  # any tag
        else:
            self.key = lookup_tag(name)
            self.tag_name = name
        self.plain = True  # no criteria but the name
        self.cls = None
        self.id = None
        self.attr_name = None
        self.attr_value = None
        self.text = None
        self.id_starts_with = None
        self.text_starts_with = None
        self.body = None
        self.body_starts_with = None
        self.source = None

    @staticmethod
    def of(name, kwargs):
        """The query from the name and the keyword arguments; an unknown keyword is a TypeError. None values are
        left out."""
        if name is not None and not isinstance(name, str):
            raise TypeError("the tag name must be a str")
        q = Query(name)
        for key, value in kwargs.items():
            if value is None:
                continue
            if key == "attr":
                if not (isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str)
                        and isinstance(value[1], str)):
                    raise TypeError("attr= takes a (name, value) tuple")
                q.attr_name = ascii_lower(value[0])
                q.attr_value = value[1]
                q.plain = False
                continue
            if key not in CRITERIA:
                raise TypeError(f"unexpected keyword argument '{key}' (the criteria are {', '.join(CRITERIA)})")
            if not isinstance(value, str):
                raise TypeError(f"{key}= takes a str")
            setattr(q, "cls" if key == "class_" else key, value)
            q.plain = False
        return q

    def name_matches_at(self, d, i):
        """Whether the tag at index i has the name of the query."""
        if self.key >= 0:
            k = d.keys[i]
            return k == self.key or (k < 0 and self.tag_name != "" and equal_fold(d.customs[i], self.tag_name))
        if not self.tag_name:
            return True
        k = d.keys[i]
        return equal_fold(NAMES[k] if k >= 0 else d.customs[i], self.tag_name)

    def matches(self, d, i):
        return self.name_matches_at(d, i) and (self.plain or self.matches_more(d, i))

    def scan(self, d, start, end, first_only):
        """The tags from index start on that match, up to the tags that end after end."""
        out = []
        ends = d.ends
        keys = d.keys
        key = self.key
        plain = self.plain
        named = self.tag_name != ""
        count = len(keys)
        i = start
        while i < count:
            if ends[i] > end:
                break
            # the name on the arrays first (a known name: one int compare)
            if key >= 0:
                k = keys[i]
                if k != key and (k >= 0 or not named or not equal_fold(d.customs[i], self.tag_name)):
                    i += 1
                    continue
            elif named and not self.name_matches_at(d, i):
                i += 1
                continue
            if plain or self.matches_more(d, i):
                out.append(i)
                if first_only:
                    break
            i += 1
        return out

    def matches_more(self, d, i):
        """The criteria other than the name."""
        if self.id is not None and (not self.id or not at.attr_equals(at.attr_raw(d, i, "id"), self.id)):
            return False
        if self.attr_name is not None and not at.attr_equals(at.attr_raw(d, i, self.attr_name), self.attr_value):
            return False
        if self.cls is not None and not at.has_class(d, i, self.cls):
            return False
        if self.id_starts_with is not None and not at.attr_starts_with(at.attr_raw(d, i, "id"), self.id_starts_with):
            return False
        if self.source is not None and d.source_of(i) != self.source:
            return False
        if self.body is not None and d.body_of(i) != self.body:
            return False
        if self.body_starts_with is not None and not d.body_of(i).startswith(self.body_starts_with):
            return False
        # the text last: it is the most expensive check
        if self.text is not None and not _text.text_equals(d.body_of(i), self.text, d, i):
            return False
        if self.text_starts_with is not None and not d.text_of(i).startswith(self.text_starts_with):
            return False
        return True


ANY = Query()
