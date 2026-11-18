"""Attributes are read from the source of the opening tag when they are asked for. The functions take the document
and the index of the tag."""

import re

from . import _decode as dec
from ._chars import ASCII_SPACE, ascii_lower, equal_fold
from ._parser import COMMENT, NAMES

# skip '<' and the name; name chars end at whitespace, '/' or '>'
NAME_END = re.compile(r"[^\t-\r />]*")
# between attributes: whitespace and '/'
GAP = re.compile(r"[\t-\r /]*")
# an attribute name ends at whitespace, '=', '/' or '>'
ATTR_NAME = re.compile(r"[^\t-\r =/>]*")

# starts a name: after whitespace, a quote, '/' or '='
NAME_BEFORE = frozenset(" \t\n\x0b\x0c\r\"'/=")
# ends a name: at whitespace, '=', '/' or '>'
NAME_AFTER = frozenset(" \t\n\x0b\x0c\r=/>")
NAME_SIZES = tuple(len(n) for n in NAMES)


def open_tag_end(d, i):
    """The position of the '>' that ends the opening tag."""
    inner_start = d.inner_starts[i]
    end = inner_start - 1 if inner_start > 0 and d.inner_ends[i] > 0 else d.ends[i]
    n = len(d.src)
    return end if end < n else n


def lower_tag(d, i):
    """The opening tag ("<name attributes", without the closing '>') in lower case (ASCII), read once."""
    cache = d.lower_tags
    t = cache.get(i)
    if t is None:
        t = ascii_lower(d.src[d.starts[i]:open_tag_end(d, i)])
        cache[i] = t
    return t


def read_value(s, pos, end):
    """Reads the value that follows an attribute name at pos (before end): (value start (-1: no '='), length,
    the position after the value)."""
    p = pos
    while p < end and s[p] in ASCII_SPACE:
        p += 1
    if p >= end or s[p] != "=":
        return -1, 0, pos
    p += 1
    while p < end and s[p] in ASCII_SPACE:
        p += 1
    q = s[p] if p < end else ""
    if q == '"' or q == "'":
        start = p + 1
        close = s.find(q, start) if start < end else -1
        if close < 0 or close >= end:
            return start, (end - start if end - start > 0 else 0), end
        return start, close - start, close + 1
    start = p
    while p < end and s[p] not in ASCII_SPACE:
        p += 1
    return start, p - start, p


class AttrScanner:
    """Reads the attributes of an opening tag one by one: positions in the source."""

    __slots__ = ("src", "end", "pos", "name_start", "name_length", "value_start", "value_length")

    def __init__(self, d, i):
        self.src = d.src
        self.end = open_tag_end(d, i)
        p = d.starts[i] + 1
        if p < self.end:
            p = NAME_END.match(self.src, p).end()
            if p > self.end:
                p = self.end
        self.pos = p
        self.name_start = 0
        self.name_length = 0
        self.value_start = -1  # -1: no value
        self.value_length = 0

    def next(self):
        """Moves to the next attribute; False at the end of the tag."""
        s = self.src
        e = self.end
        while True:
            if self.pos < e:
                p = GAP.match(s, self.pos).end()
                self.pos = p if p < e else e
            if self.pos >= e:
                return False
            self.name_start = self.pos
            p = ATTR_NAME.match(s, self.pos).end()
            self.pos = p if p < e else e
            self.name_length = self.pos - self.name_start
            if self.name_length == 0:
                self.pos += 1  # a stray '=': skip it
                continue
            self.value_start, self.value_length, self.pos = read_value(s, self.pos, e)
            return True

    def name(self):
        return self.src[self.name_start:self.name_start + self.name_length]

    def raw_value(self):
        if self.value_start < 0:
            return ""
        return self.src[self.value_start:self.value_start + self.value_length]


def attr_value(d, i, name):
    """The decoded value of the first attribute with the name (lower case); "" when missing or without a value."""
    raw = attr_raw(d, i, name)
    return dec.decode(raw, True) if "&" in raw or "\r" in raw else raw


def has_class(d, i, cls):
    """Whether one of the space-separated classes equals cls (case-insensitive)."""
    if not cls:
        return False
    value = attr_raw(d, i, "class")
    if "&" in value or "\r" in value:
        value = dec.decode(value, True)
    vl = len(value)
    cl = len(cls)
    if vl == cl:
        return equal_fold(value, cls)
    if cl > vl:
        return False
    lv = ascii_lower(value)
    lc = ascii_lower(cls)
    start = 0
    while start <= vl - cl:
        k = lv.find(lc, start)
        if k < 0:
            return False
        after = k + cl
        if (k == 0 or lv[k - 1] in ASCII_SPACE) and (after == vl or lv[after] in ASCII_SPACE):
            return True
        start = k + 1
    return False


def attr_raw(d, i, attribute):
    """The raw value of the first attribute with the name (lower case), as a browser reads the tag: quoted or
    unquoted (href=/p?a=1), "" for an attribute without a value or a missing one."""
    # a comment has no attributes, whatever its text looks like
    key = d.keys[i]
    if not attribute or key == COMMENT:
        return ""

    # the opening tag: "<name attributes", without the closing '>', in lower case
    tag = lower_tag(d, i)
    length = len(tag)

    # The first place where the name stands as a word of its own. Most tags have none.
    alen = len(attribute)
    p = tag.find(attribute, 1)
    while p >= 0:
        after = p + alen
        if tag[p - 1] in NAME_BEFORE and (after == length or tag[after] in NAME_AFTER):
            break
        p = tag.find(attribute, p + 1) if p + 1 < length else -1
    if p < 0:
        return ""

    src = d.src
    start = d.starts[i]
    # That word is the attribute unless it is inside the value of another one (title="see id=5", x= id).
    # Nearly always that is ruled out at once, and the value is read in place.
    if (key >= 0 and p == NAME_SIZES[key] + 2) or _name_for_sure(tag, p):
        q = p + alen
        while q < length and tag[q] in ASCII_SPACE:
            q += 1
        if q >= length or tag[q] != "=":
            return ""  # no value
        q += 1
        while q < length and tag[q] in ASCII_SPACE:
            q += 1
        if q >= length:
            return ""
        c = tag[q]
        if c == '"' or c == "'":
            close = tag.find(c, q + 1)
            if close < 0:
                return src[start + q + 1:start + length]
            return src[start + q + 1:start + close]
        v = q
        while v < length and tag[v] not in ASCII_SPACE:
            v += 1
        return src[start + q:start + v]
    pos = start + p

    # Otherwise the attributes are read one by one, up to the one that starts at that word.
    s = AttrScanner(d, i)
    while s.next():
        # a word the scanner has passed was inside a value: the next one takes its place
        while pos < s.name_start:
            pos = _next_word(tag, start, attribute, pos + 1)
            if pos < 0:
                return ""
        if pos == s.name_start and s.name_length == alen:
            return "" if s.value_start < 0 else src[s.value_start:s.value_start + s.value_length]
    return ""


def _next_word(tag, start, attribute, at):
    """The next place in the opening tag (tag, lower case, at start in the source) at or after the source position
    at where the name stands as a word of its own, or -1."""
    n = len(attribute)
    length = len(tag)
    i = at - start
    while i < length:
        found = tag.find(attribute, i)
        if found < 0:
            break
        after = found + n
        if tag[found - 1] in NAME_BEFORE and (after == length or tag[after] in NAME_AFTER):
            return start + found
        i = found + 1
    return -1


def _name_for_sure(tag, pos):
    """Whether the word at pos of the opening tag is an attribute name, judged by the chars before it alone. False
    means "not sure"."""
    # After whitespace, and not as the unquoted value in "x= word".
    if tag[pos - 1] not in ASCII_SPACE:
        return False
    e = pos
    while e > 0 and tag[e - 1] in ASCII_SPACE:
        e -= 1
    if tag[e - 1] == "=":
        return False
    # Not inside a quoted value: it is enough that the last quote of each kind before the word does not open a value.
    for quote in ('"', "'"):
        k = tag.rfind(quote, 0, e)
        if k < 0:
            continue
        j = k
        while j > 0 and tag[j - 1] in ASCII_SPACE:
            j -= 1
        if j > 0 and tag[j - 1] == "=":
            return False
    return True


def attr_equals(raw, value):
    """Compares the decoded value; decodes only when the raw value has '&' or CR."""
    return (dec.decode(raw, True) if "&" in raw or "\r" in raw else raw) == value


def attr_starts_with(raw, prefix):
    return (dec.decode(raw, True) if "&" in raw or "\r" in raw else raw).startswith(prefix)


def has_attribute(d, i, name):
    """Whether the element has the attribute (lower case), with or without a value."""
    if attr_raw(d, i, name):
        return True
    if d.keys[i] == COMMENT:
        return False
    s = AttrScanner(d, i)
    while s.next():
        if equal_fold(s.name(), name):
            return True
    return False
