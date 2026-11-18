"""The public classes: Document, Node, CssQuery and XPathQuery."""

import re
import threading

from . import _attrs as at
from . import _decode as dec
from . import _links
from . import _text
from ._chars import SPACE, ByteOffsets, ascii_lower, trim_range
from ._css import CssMatcher, CssSelectorError, CssTree
from ._parser import BOM, COMMENT, NAMES, is_raw_text, parse_tags
from ._query import ANY, Query
from ._xpath import XPathError, XPathMatcher

INF = float("inf")
TEXT_SPECIAL = re.compile(r"[<&\r]")

# ----- query caches: the string methods compile a query once -----

_lock = threading.Lock()
_css_cache = {}
_xpath_cache = {}


def _cached(cache, key, compile_):
    q = cache.get(key)
    if q is not None:
        return q
    q = compile_(key)
    with _lock:
        if len(cache) >= 1024:
            cache.clear()  # dynamically built queries must not grow the cache forever
        cache[key] = q
    return q


def _css(selector):
    if not isinstance(selector, str):
        raise TypeError("a CSS selector must be a str")
    return _cached(_css_cache, selector, CssMatcher)


def _xpath(expression):
    if not isinstance(expression, str):
        raise TypeError("an XPath expression must be a str")
    return _cached(_xpath_cache, expression, XPathMatcher)


def _source_text(html):
    if isinstance(html, str):
        return html
    if isinstance(html, (bytes, bytearray, memoryview)):
        return bytes(html).decode("utf-8", "replace")
    raise TypeError("parse() takes a str or bytes")


def parse(html, url=None):
    """Parses an HTML or XML page. Bytes are read as UTF-8 (invalid sequences become U+FFFD).

    With url, relative links (Node.href, Node.src) are resolved against it.
    """
    return Document(html, url)


def decode_html(text):
    """Decodes the character references of a text (CR LF and a lone CR become LF)."""
    if not isinstance(text, str):
        raise TypeError("decode_html() takes a str")
    return dec.decode(text, False)


def html_to_text(html):
    """The visible text of an HTML fragment: tags removed, whitespace collapsed, entities decoded."""
    if not isinstance(html, str):
        raise TypeError("html_to_text() takes a str")
    return _text.clear_tags(html)


def html_to_text_line_breaks(html):
    """The text of an HTML fragment with the visible line structure kept."""
    if not isinstance(html, str):
        raise TypeError("html_to_text_line_breaks() takes a str")
    return _text.clear_tags_line_breaks(html)


class Document:
    """A parsed HTML or XML page: a flat list of tags in document order.

    `len(doc)`, `doc[i]` (negative indexes too) and iteration give the tags, comments included.
    """

    __slots__ = ("src", "_url", "keys", "customs", "parents", "depths", "starts", "ends", "inner_starts",
                 "inner_ends", "lower_tags", "_tree", "_base", "_base_parsed", "_offsets", "__weakref__")

    def __init__(self, html, url=None):
        src = _source_text(html)
        if src.startswith(BOM):
            src = src[1:]
        if url is not None and not isinstance(url, str):
            raise TypeError("url must be a str")
        self.src = src
        self._url = url or ""
        (self.keys, self.customs, self.parents, self.depths, self.starts, self.ends, self.inner_starts,
         self.inner_ends) = parse_tags(src)
        self.lower_tags = {}
        self._tree = None
        self._base = None
        self._base_parsed = False
        self._offsets = None

    # ----- internals shared by the query engines -----

    def css_tree(self):
        t = self._tree
        if t is None:
            t = self._tree = CssTree(self)
        return t

    def base(self):
        """The page URL parsed once, for resolving relative links."""
        if not self._base_parsed:
            self._base = _links.absolute_url(self._url)
            self._base_parsed = True
        return self._base

    def byte_offset(self, i):
        if self._offsets is None:
            self._offsets = ByteOffsets(self.src)
        return self._offsets(i)

    def name_of(self, i):
        k = self.keys[i]
        return NAMES[k] if k >= 0 else self.customs[i]

    def body_of(self, i):
        start = self.inner_starts[i]
        end = self.inner_ends[i]
        if start < 0 or end <= 0:
            return ""
        return self.src[start:end + 1].strip(SPACE)

    def text_of(self, i):
        body = self.body_of(i)
        # no tags, references or CR: the body is the text
        if TEXT_SPECIAL.search(body) is None:
            return body
        # No child tags and no '<' at all: the body is the text, except in script, style, textarea, title, ...
        if not self.has_children_at(i) and ("<" not in body or is_raw_text(self.keys[i])):
            return dec.decode(body, False) if "&" in body or "\r" in body else body
        return _text.clear_tags(body)

    def has_children_at(self, i):
        return i + 1 < len(self.starts) and self.starts[i + 1] < self.ends[i]

    def source_of(self, i):
        src = self.src
        start = self.starts[i]
        end = self.ends[i]
        if self.inner_starts[i] > 0 and self.inner_ends[i] > 0:
            length = self.inner_starts[i] - start - 2
        elif 0 < end and end - 1 < len(src) and src[end - 1] == "/":
            length = end - start - 2
        else:
            length = end - start - 1
        return trim_range(src, start + 1, start + 1 + length)

    def next_matching(self, i, q):
        starts = self.starts
        end = self.ends[i]
        for k in range(i + 1, len(starts)):
            if starts[k] > end and q.matches(self, k):
                return k
        return -1

    def sibling_matching(self, i, q, forward):
        # deeper tags are inside the siblings; a shallower one ends (or is) the parent
        depths = self.depths
        depth = depths[i]
        count = len(depths)
        step = 1 if forward else -1
        k = i + step
        while 0 <= k < count and depths[k] >= depth:
            if depths[k] == depth and q.matches(self, k):
                return k
            k += step
        return -1

    def _node(self, i):
        return Node(self, i) if i >= 0 else None

    def _nodes(self, indexes):
        return [Node(self, i) for i in indexes]

    # ----- the public API -----

    @property
    def url(self):
        """The page URL given to parse(), or ""."""
        return self._url

    @property
    def source(self):
        """The page source."""
        return self.src

    def __len__(self):
        return len(self.keys)

    def __getitem__(self, index):
        if not isinstance(index, int):
            raise TypeError("tag indexes must be integers")
        n = len(self.keys)
        i = index + n if index < 0 else index
        if i < 0 or i >= n:
            raise IndexError("tag index out of range")
        return Node(self, i)

    def __iter__(self):
        for i in range(len(self.keys)):
            yield Node(self, i)

    def __repr__(self):
        return f"<Document url={self._url!r} tags={len(self.keys)}>"

    def find(self, name=None, **criteria):
        """The first tag that matches: by name (any name, case-insensitive) and the criteria; or None."""
        r = Query.of(name, criteria).scan(self, 0, INF, True)
        return Node(self, r[0]) if r else None

    def find_all(self, name=None, **criteria):
        """All tags that match, in document order."""
        return self._nodes(Query.of(name, criteria).scan(self, 0, INF, False))

    def css(self, selector):
        """The tags that match the CSS selector (querySelectorAll). Raises CssSelectorError on an invalid one."""
        return self._nodes(_css(selector).scan(self, None, 0, INF, False))

    def css_first(self, selector):
        """The first tag that matches the CSS selector (querySelector), or None."""
        r = _css(selector).scan(self, None, 0, INF, True)
        return Node(self, r[0]) if r else None

    def xpath(self, expression):
        """The tags the XPath 1.0 expression selects."""
        return self._nodes(_xpath(expression).tags(self, None))

    def xpath_first(self, expression):
        """The first tag the XPath expression selects, or None."""
        r = _xpath(expression).tags(self, None)
        return Node(self, r[0]) if r else None

    def xpath_values(self, expression):
        """The values the XPath expression selects: decoded attributes, text(), visible text of tags, or the result
        of count(), sum(), string(), ..."""
        return _xpath(expression).values(self, None)

    def xpath_value(self, expression):
        """The first value the XPath expression selects, or ""."""
        v = _xpath(expression).value(self, None)
        return "" if v is None else v


class Node:
    """One tag of a document. Positions (pos) are byte offsets into the UTF-8 source.

    Nodes compare equal when they are the same tag of the same document, and are hashable.
    """

    __slots__ = ("_doc", "_i")

    def __init__(self, doc, index):
        self._doc = doc
        self._i = index

    @property
    def name(self):
        """The lower-case tag name ("div", "!--" for comments, "custom-tag", ...)."""
        return self._doc.name_of(self._i)

    @property
    def index(self):
        """The position of the tag in the document."""
        return self._i

    @property
    def depth(self):
        """The nesting depth: 0 for top-level tags."""
        return self._doc.depths[self._i]

    @property
    def pos(self):
        """The byte offsets of the opening '<' and of the last '>' in the UTF-8 source."""
        d = self._doc
        # the end is the last byte of the last char (a cut-off page ends inside the text)
        return d.byte_offset(d.starts[self._i]), d.byte_offset(d.ends[self._i] + 1) - 1

    @property
    def text(self):
        """The visible text: tags, comments, script and style removed, entities decoded, whitespace collapsed."""
        return self._doc.text_of(self._i)

    @property
    def text_line_breaks(self):
        """The text with the visible line structure kept: <br> and block elements give new lines, p and headings
        paragraphs."""
        d = self._doc
        if d.inner_starts[self._i] < 0 or d.inner_ends[self._i] <= 0:
            return ""
        return _text.clear_tags_line_breaks(d.body_of(self._i))

    @property
    def body(self):
        """The inner HTML (trimmed); "" for tags that are not closed."""
        return self._doc.body_of(self._i)

    @property
    def outer_html(self):
        """The source of the whole element; "" for tags that are not closed."""
        d = self._doc
        i = self._i
        if d.inner_starts[i] <= 0 or d.inner_ends[i] <= 0:
            return ""
        return trim_range(d.src, d.starts[i], d.ends[i] + 1)

    @property
    def source(self):
        """The opening tag without '<' and '>': the name and the attributes."""
        return self._doc.source_of(self._i)

    @property
    def attrs(self):
        """The attributes as a dict of decoded values, names in lower case (the first of a repeated name wins)."""
        d = self._doc
        i = self._i
        out = {}
        if d.keys[i] == COMMENT:
            return out
        s = at.AttrScanner(d, i)
        while s.next():
            name = s.name()
            key = name.lower()
            if key not in out:
                out[key] = at.attr_value(d, i, ascii_lower(name))
        return out

    def attr(self, name):
        """The decoded value of the first attribute with the name (case-insensitive); "" when it is missing."""
        return at.attr_value(self._doc, self._i, ascii_lower(name))

    def attr_raw(self, name):
        """The value of the attribute as written in the source."""
        return at.attr_raw(self._doc, self._i, ascii_lower(name))

    def has_class(self, class_):
        """Whether one of the space-separated classes equals class_ (case-insensitive)."""
        return at.has_class(self._doc, self._i, class_)

    @property
    def href(self):
        """The link a browser follows: href decoded and made absolute against the page URL. An empty or missing
        href gives the page URL."""
        d = self._doc
        return _links.correct_link(at.attr_raw(d, self._i, "href"), d.url, d.base())

    @property
    def src(self):
        """src decoded and made absolute against the page URL."""
        d = self._doc
        return _links.correct_link(at.attr_raw(d, self._i, "src"), d.url, d.base())

    def href_from(self, base_url):
        """href resolved against base_url instead of the page URL."""
        return _links.correct_link(at.attr_raw(self._doc, self._i, "href"), base_url, _links.absolute_url(base_url))

    def src_from(self, base_url):
        """src resolved against base_url instead of the page URL."""
        return _links.correct_link(at.attr_raw(self._doc, self._i, "src"), base_url, _links.absolute_url(base_url))

    @property
    def mailto(self):
        """The address of a mailto: link, or ""."""
        return _links.after_scheme(at.attr_raw(self._doc, self._i, "href"), "mailto")

    @property
    def tel(self):
        """The number of a tel: link, or ""."""
        return _links.after_scheme(at.attr_raw(self._doc, self._i, "href"), "tel")

    @property
    def text_after(self):
        """The source text right after the tag up to the next '<'."""
        src = self._doc.src
        start = self._doc.ends[self._i] + 1
        if start >= len(src):
            return ""
        nxt = src.find("<", start)
        return src[start:] if nxt < 0 else src[start:nxt]

    # ----- navigation -----

    @property
    def parent(self):
        """The enclosing tag, or None."""
        return self._doc._node(self._doc.parents[self._i])

    @property
    def children(self):
        """The direct children."""
        d = self._doc
        ends = d.ends
        depths = d.depths
        depth = depths[self._i] + 1
        end = ends[self._i]
        out = []
        i = self._i + 1
        count = len(ends)
        while i < count and ends[i] <= end:
            if depths[i] == depth:
                out.append(Node(d, i))
            i += 1
        return out

    @property
    def has_children(self):
        """Whether another tag starts inside this one."""
        return self._doc.has_children_at(self._i)

    @property
    def next(self):
        """The tag that follows in document order (the first child, if there is one), or None."""
        i = self._i + 1
        return Node(self._doc, i) if i < len(self._doc.keys) else None

    @property
    def previous(self):
        """The tag before this one in document order, or None."""
        return Node(self._doc, self._i - 1) if self._i > 0 else None

    @property
    def next_sibling(self):
        """The next tag at the same level inside the same parent, or None."""
        return self._doc._node(self._doc.sibling_matching(self._i, ANY, True))

    @property
    def previous_sibling(self):
        """The previous tag at the same level inside the same parent, or None."""
        return self._doc._node(self._doc.sibling_matching(self._i, ANY, False))

    # ----- search -----

    def find(self, name=None, **criteria):
        """The first tag inside this one that matches, or None."""
        d = self._doc
        r = Query.of(name, criteria).scan(d, self._i + 1, d.ends[self._i], True)
        return Node(d, r[0]) if r else None

    def find_all(self, name=None, **criteria):
        """All tags inside this one that match."""
        d = self._doc
        return d._nodes(Query.of(name, criteria).scan(d, self._i + 1, d.ends[self._i], False))

    def matches(self, name=None, **criteria):
        """Whether this tag matches."""
        return Query.of(name, criteria).matches(self._doc, self._i)

    def find_parent(self, name=None, **criteria):
        """The nearest enclosing tag that matches, or None."""
        q = Query.of(name, criteria)
        d = self._doc
        p = d.parents[self._i]
        while p >= 0:
            if q.matches(d, p):
                return Node(d, p)
            p = d.parents[p]
        return None

    def find_next(self, name=None, **criteria):
        """The first tag after the end of this one that matches (tags inside it are skipped), or None."""
        return self._doc._node(self._doc.next_matching(self._i, Query.of(name, criteria)))

    def find_next_sibling(self, name=None, **criteria):
        """The next sibling that matches, or None."""
        return self._doc._node(self._doc.sibling_matching(self._i, Query.of(name, criteria), True))

    def find_previous_sibling(self, name=None, **criteria):
        """The nearest previous sibling that matches, or None."""
        return self._doc._node(self._doc.sibling_matching(self._i, Query.of(name, criteria), False))

    def text_after_until(self, name=None, **criteria):
        """The source between the end of this tag and the next tag that matches; "" when there is none."""
        d = self._doc
        k = d.next_matching(self._i, Query.of(name, criteria))
        if k < 0:
            return ""
        start = d.ends[self._i] + 1
        end = d.starts[k]
        return d.src[start:end] if start < end else ""

    def css(self, selector):
        """The tags inside this one that match the CSS selector; it may start with '>' for the children."""
        d = self._doc
        return d._nodes(_css(selector).scan(d, self._i, self._i + 1, d.ends[self._i], False))

    def css_first(self, selector):
        """The first tag inside this one that matches the CSS selector, or None."""
        d = self._doc
        r = _css(selector).scan(d, self._i, self._i + 1, d.ends[self._i], True)
        return Node(d, r[0]) if r else None

    def css_matches(self, selector):
        """Whether this tag matches the CSS selector (element.matches())."""
        return _css(selector).matches(self._doc, self._i)

    def xpath(self, expression):
        """The tags the XPath expression selects, relative paths starting at this tag."""
        return self._doc._nodes(_xpath(expression).tags(self._doc, self._i))

    def xpath_first(self, expression):
        """The first tag the XPath expression selects from this tag, or None."""
        r = _xpath(expression).tags(self._doc, self._i)
        return Node(self._doc, r[0]) if r else None

    def xpath_values(self, expression):
        """The values the XPath expression selects from this tag."""
        return _xpath(expression).values(self._doc, self._i)

    def xpath_value(self, expression):
        """The first value the XPath expression selects from this tag, or ""."""
        v = _xpath(expression).value(self._doc, self._i)
        return "" if v is None else v

    def __eq__(self, other):
        return isinstance(other, Node) and other._doc is self._doc and other._i == self._i

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash((id(self._doc), self._i))

    def __repr__(self):
        source = self.source
        short = source[:80]
        more = "..." if len(short) < len(source) else ""
        return f"<Node {self._i} <{short}{more}>>"


def _target(obj):
    if isinstance(obj, Document):
        return obj, None
    if isinstance(obj, Node):
        return obj._doc, obj._i
    raise TypeError("expected a Document or a Node")


class CssQuery:
    """A compiled CSS selector: compile once, use on any number of pages and threads."""

    __slots__ = ("_m",)

    def __init__(self, selector):
        if not isinstance(selector, str):
            raise TypeError("a CSS selector must be a str")
        self._m = CssMatcher(selector)

    @property
    def selector(self):
        return self._m.selector

    def all(self, target, /):
        """The matching tags in a document, or inside a node."""
        d, i = _target(target)
        if i is None:
            return d._nodes(self._m.scan(d, None, 0, INF, False))
        return d._nodes(self._m.scan(d, i, i + 1, d.ends[i], False))

    def first(self, target, /):
        """The first matching tag in a document, or inside a node, or None."""
        d, i = _target(target)
        if i is None:
            r = self._m.scan(d, None, 0, INF, True)
        else:
            r = self._m.scan(d, i, i + 1, d.ends[i], True)
        return Node(d, r[0]) if r else None

    def matches(self, node, /):
        """Whether the tag matches (element.matches())."""
        if not isinstance(node, Node):
            raise TypeError("expected a Node")
        return self._m.matches(node._doc, node._i)

    def __repr__(self):
        return f"CssQuery({self._m.selector!r})"


class XPathQuery:
    """A compiled XPath 1.0 expression: compile once, use on any number of pages and threads."""

    __slots__ = ("_m",)

    def __init__(self, expression):
        if not isinstance(expression, str):
            raise TypeError("an XPath expression must be a str")
        self._m = XPathMatcher(expression)

    @property
    def expression(self):
        return self._m.expression

    @property
    def is_node_set(self):
        """Whether the expression gives tags (a node-set), not a number, string or boolean."""
        return self._m.is_node_set

    def nodes(self, target, /):
        """The selected tags, evaluated on a document or from a node."""
        d, i = _target(target)
        return d._nodes(self._m.tags(d, i))

    def values(self, target, /):
        """The values of the selected nodes, or the result of the expression as one value."""
        d, i = _target(target)
        return self._m.values(d, i)

    def value(self, target, /):
        """The value of the first selected node, or the result of the expression; None when it selects nothing."""
        d, i = _target(target)
        return self._m.value(d, i)

    def __repr__(self):
        return f"XPathQuery({self._m.expression!r})"


__all__ = ["CssQuery", "CssSelectorError", "Document", "Node", "XPathError", "XPathQuery", "decode_html",
           "html_to_text", "html_to_text_line_breaks", "parse"]
