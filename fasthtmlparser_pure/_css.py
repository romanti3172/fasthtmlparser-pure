"""CSS selectors (Selectors Level 3 and the common Level 4 pseudo-classes), matched right to left over the flat tag
list as browsers do. Tags are document indexes; the scope and the anchor are an index or None (the document)."""

import re

from . import _attrs as at
from . import _decode as dec
from ._chars import ascii_lower, equal_fold, is_alpha, is_digit, is_hex, lower, quote
from ._parser import COMMENT, DOCTYPE, XML_DECLARATION, lookup_tag


class CssSelectorError(ValueError):
    """An invalid CSS selector."""


# combinators
COMB_NONE = 0
COMB_DESCENDANT = 1  # "a b"
COMB_CHILD = 2  # "a > b"
COMB_ADJACENT = 3  # "a + b"
COMB_SIBLING = 4  # "a ~ b"

# simple selectors: (kind, ...)
S_ID = 0  # (S_ID, id)
S_CLASS = 1  # (S_CLASS, name)
S_ATTR = 2  # (S_ATTR, name, op, value, fold)
S_NTH = 3  # (S_NTH, a, b, from_end, of_type, of (a list or None))
S_NOT = 4  # (S_NOT, list)
S_IS = 5  # (S_IS, list)
S_HAS = 6  # (S_HAS, list)
S_CONTAINS = 7  # (S_CONTAINS, text)
S_PSEUDO = 8  # (S_PSEUDO, kind)

OP_EXISTS = 0  # [a]
OP_EQUALS = 1  # [a=v]
OP_INCLUDES = 2  # [a~=v]
OP_DASH = 3  # [a|=v]
OP_PREFIX = 4  # [a^=v]
OP_SUFFIX = 5  # [a$=v]
OP_SUBSTRING = 6  # [a*=v]

PSEUDO_ROOT = 0
PSEUDO_SCOPE = 1
PSEUDO_EMPTY = 2
PSEUDO_LINK = 3
PSEUDO_CHECKED = 4
PSEUDO_DISABLED = 5
PSEUDO_ENABLED = 6
PSEUDO_REQUIRED = 7
PSEUDO_OPTIONAL = 8
PSEUDOS = {
    "root": PSEUDO_ROOT, "scope": PSEUDO_SCOPE, "empty": PSEUDO_EMPTY, "link": PSEUDO_LINK, "any-link": PSEUDO_LINK,
    "checked": PSEUDO_CHECKED, "disabled": PSEUDO_DISABLED, "enabled": PSEUDO_ENABLED, "required": PSEUDO_REQUIRED,
    "optional": PSEUDO_OPTIONAL,
}

CSS_SPACE = " \t\n\x0c\r"
CSS_SPACE_RE = re.compile(r"[ \t\n\f\r]")

# HTML: the values of these attributes are compared ignoring ASCII case unless the selector has the 's' flag.
CASE_INSENSITIVE_VALUES = frozenset((
    "accept accept-charset align alink axis bgcolor charset checked clear codetype color compact declare defer dir "
    "direction disabled enctype face frame hreflang http-equiv lang language link media method multiple nohref "
    "noresize noshade nowrap readonly rel rev rules scope scrolling selected shape target text type valign valuetype "
    "vlink").split())

FORM_CONTROLS = frozenset("button input select textarea optgroup option fieldset".split())
INPUTS = frozenset("input select textarea".split())

REPLACEMENT = chr(0xFFFD)


class Complex:
    """A complex selector: lead combinator, compounds c, combinators k (k[i] stands between c[i] and c[i+1])."""

    __slots__ = ("lead", "c", "k")

    def __init__(self):
        self.lead = COMB_NONE
        self.c = []
        self.k = []


class Compound:
    """Tag key or -1, custom name ("": any element when key is -1), simple selectors."""

    __slots__ = ("key", "name", "s")

    def __init__(self):
        self.key = -1
        self.name = ""
        self.s = []


def element_at(d, i):
    """Not a comment, the doctype or the XML declaration."""
    key = d.keys[i]
    if key >= 0:
        return key != COMMENT and key != DOCTYPE and key != XML_DECLARATION
    custom = d.customs[i]
    return custom != "" and custom[0] != "!" and custom[0] != "?"


# ----- the compiler -----

class _Compiler:
    def __init__(self, s):
        self.s = s
        self.n = len(s)
        self.p = 0

    def fail(self, msg):
        raise CssSelectorError(f"invalid CSS selector {quote(self.s)} at position {self.p}: {msg}")

    def peek(self, offset):
        i = self.p + offset
        return self.s[i] if i < self.n else ""

    def skip_space(self):
        k = self.p
        while self.p < self.n and self.s[self.p] in CSS_SPACE:
            self.p += 1
        return self.p > k

    def expect(self, ch):
        if self.peek(0) != ch:
            if self.p >= self.n:
                self.fail(f"'{ch}' expected")
            self.fail(f"'{ch}' expected instead of '{self.s[self.p]}'")
        self.p += 1

    def parse_list(self, relative, top_level):
        """selector-list: complex (',' complex)*"""
        out = []
        while True:
            out.append(self.parse_complex(relative, top_level))
            self.skip_space()
            if self.peek(0) != ",":
                return out
            self.p += 1

    def parse_complex(self, relative, top_level):
        """complex: [combinator] compound (combinator compound)*"""
        self.skip_space()
        cx = Complex()
        if relative or top_level:
            comb = self.read_combinator()
            if comb != COMB_NONE:
                if top_level and comb != COMB_CHILD:
                    self.fail("a selector can start only with '>' (the children of the tag it is called on)")
                cx.lead = comb
            elif relative:
                cx.lead = COMB_DESCENDANT
        cx.c.append(self.parse_compound())
        while True:
            ws = self.skip_space()
            ch = self.peek(0)
            if ch == "" or ch == "," or ch == ")":
                return cx
            comb = self.read_combinator()
            if comb == COMB_NONE:
                if not ws:
                    self.fail(f"unexpected '{self.s[self.p]}'")
                comb = COMB_DESCENDANT
            cx.k.append(comb)
            cx.c.append(self.parse_compound())

    def read_combinator(self):
        c = self.peek(0)
        comb = COMB_CHILD if c == ">" else COMB_ADJACENT if c == "+" else COMB_SIBLING if c == "~" else COMB_NONE
        if comb != COMB_NONE:
            self.p += 1
            self.skip_space()
        return comb

    def parse_compound(self):
        """compound: [type | '*'] (#id | .class | [attribute] | :pseudo)*"""
        cp = Compound()
        has_type = False
        if self.peek(0) == "*":
            self.p += 1
            has_type = True
        elif self.ident_start(self.p):
            name = lower(self.read_name())
            has_type = True
            t = lookup_tag(name)
            if t >= 0:
                cp.key = t
            else:
                cp.name = name
        if self.peek(0) == "|":
            self.fail("namespaces are not supported; escape the colon of a prefixed name: dc\\:creator")

        while True:
            ch = self.peek(0)
            if ch == "#":
                self.p += 1
                if not self.name_char(self.p):
                    self.fail("an id expected after '#'")
                cp.s.append((S_ID, self.read_name()))
            elif ch == ".":
                self.p += 1
                if not self.ident_start(self.p):
                    self.fail("a class name expected after '.'")
                cp.s.append((S_CLASS, self.read_name()))
            elif ch == "[":
                cp.s.append(self.parse_attribute())
            elif ch == ":":
                self.parse_pseudo(cp)
            else:
                break

        if not has_type and not cp.s:
            if self.p >= self.n:
                self.fail("a selector expected")
            self.fail(f"unexpected '{self.s[self.p]}'")
        return cp

    def parse_attribute(self):
        """'[' name [op value [i|s]] ']'"""
        self.p += 1  # '['
        self.skip_space()
        if not self.ident_start(self.p):
            self.fail("an attribute name expected")
        name = self.read_name()
        self.skip_space()
        if self.peek(0) == "]":
            self.p += 1
            return attribute_selector(name, "", "", None)
        ch = self.peek(0)
        if ch == "=":
            op = "="
            self.p += 1
        elif ch != "" and ch in "~|^$*":
            op = ch
            if self.peek(1) != "=":
                self.fail(f"'=' expected after '{op}'")
            self.p += 2
        else:
            self.fail("'=', '~=', '|=', '^=', '$=', '*=' or ']' expected")

        self.skip_space()
        if self.peek(0) == '"' or self.peek(0) == "'":
            value = self.read_string()
        elif self.ident_start(self.p):
            value = self.read_name()
        else:
            self.fail("an attribute value expected (quote it if it is not a plain word)")

        self.skip_space()
        flag = None
        if self.ident_start(self.p):
            f = self.read_name()
            if equal_fold(f, "i"):
                flag = True
            elif equal_fold(f, "s"):
                flag = False
            else:
                self.fail(f"unknown attribute flag '{f}'")
            self.skip_space()
        self.expect("]")
        return attribute_selector(name, op, value, flag)

    def parse_pseudo(self, cp):
        self.p += 1  # ':'
        if self.peek(0) == ":":
            self.fail("pseudo-elements (::before, ::text) select no tags")
        if not self.ident_start(self.p):
            self.fail("a pseudo-class name expected after ':'")
        start = self.p
        name = lower(self.read_name())

        if self.peek(0) == "(":
            self.p += 1
            self.skip_space()
            if name == "not":
                cp.s.append((S_NOT, self.parse_list(False, False)))
            elif name in ("is", "where", "matches", "any"):
                cp.s.append((S_IS, self.parse_list(False, False)))
            elif name == "has":
                cp.s.append((S_HAS, self.parse_list(True, False)))
            elif name in ("nth-child", "nth-last-child", "nth-of-type", "nth-last-of-type"):
                a, b = self.parse_nth()
                of_type = name.endswith("of-type")
                of = None
                self.skip_space()
                if not of_type and self.word("of"):
                    self.p += 2
                    of = self.parse_list(False, False)
                cp.s.append((S_NTH, a, b, "last" in name, of_type, of))
            elif name == "contains":
                if self.peek(0) == '"' or self.peek(0) == "'":
                    text = self.read_string()
                elif self.ident_start(self.p):
                    text = self.read_name()
                else:
                    self.fail("a string expected in :contains()")
                cp.s.append((S_CONTAINS, text))
            else:
                self.p = start
                self.fail(f"unknown pseudo-class ':{name}()'")
            self.skip_space()
            self.expect(")")
            return

        if name == "first-child":
            cp.s.append((S_NTH, 0, 1, False, False, None))
        elif name == "last-child":
            cp.s.append((S_NTH, 0, 1, True, False, None))
        elif name == "only-child":
            cp.s.append((S_NTH, 0, 1, False, False, None))
            cp.s.append((S_NTH, 0, 1, True, False, None))
        elif name == "first-of-type":
            cp.s.append((S_NTH, 0, 1, False, True, None))
        elif name == "last-of-type":
            cp.s.append((S_NTH, 0, 1, True, True, None))
        elif name == "only-of-type":
            cp.s.append((S_NTH, 0, 1, False, True, None))
            cp.s.append((S_NTH, 0, 1, True, True, None))
        else:
            kind = PSEUDOS.get(name)
            if kind is None:
                self.p = start
                self.fail(f"unknown pseudo-class ':{name}'")
            cp.s.append((S_PSEUDO, kind))

    def parse_nth(self):
        """An+B: odd, even, 3, n, -n+3, 2n+1, +2n - 1; (a, b)"""
        if self.word("odd"):
            self.p += 3
            return 2, 1
        if self.word("even"):
            self.p += 4
            return 2, 0
        start = self.p
        t = []
        while self.p < self.n:
            ch = self.s[self.p]
            if not (ch in "+-nN" or is_digit(ch) or ch in CSS_SPACE):
                break
            if ch not in CSS_SPACE:
                t.append("n" if ch == "N" else ch)
            self.p += 1
        t = "".join(t)
        n = t.find("n")
        if n < 0:
            v = _parse_int32(t)
            if v is not None:
                return 0, v
        elif t.find("n", n + 1) < 0:
            a_text = t[:n]
            b_text = t[n + 1:]
            if a_text == "" or a_text == "+":
                a = 1
            else:
                a = -1 if a_text == "-" else _parse_int32(a_text)
            b = 0
            if b_text:
                ok = b_text[0] in "+-" and len(b_text) > 1 and is_digit(b_text[1])
                b = _parse_int32(b_text) if ok else None
            if a is not None and b is not None:
                return a, b
        self.p = start
        self.fail("An+B expected (odd, even, 3, 2n+1, -n+3)")

    def word(self, w):
        """The identifier at the position is w (case-insensitive) and ends there."""
        n = len(w)
        return self.p + n <= self.n and ascii_lower(self.s[self.p:self.p + n]) == w and not self.name_char(self.p + n)

    def escape(self, p):
        if p >= self.n or self.s[p] != "\\":
            return False
        if p + 1 >= self.n:
            return True
        c = self.s[p + 1]
        return c != "\n" and c != "\r" and c != "\x0c"

    @staticmethod
    def name_start_char(c):
        return is_alpha(c) or c == "_" or c >= "\x80"

    def name_char(self, p):
        if p >= self.n:
            return False
        c = self.s[p]
        return self.name_start_char(c) or is_digit(c) or c == "-" or self.escape(p)

    def ident_start(self, p):
        if p >= self.n:
            return False
        if self.s[p] == "-":
            p += 1
            if p >= self.n:
                return False
            c = self.s[p]
            return self.name_start_char(c) or c == "-" or self.escape(p)
        return self.name_start_char(self.s[p]) or self.escape(p)

    def read_name(self):
        b = []
        while self.name_char(self.p):
            if self.s[self.p] == "\\":
                b.append(self.read_escape())
            else:
                b.append(self.s[self.p])
                self.p += 1
        return "".join(b)

    def read_string(self):
        q = self.s[self.p]
        start = self.p
        self.p += 1
        b = []
        while True:
            if self.p >= self.n:
                self.p = start
                self.fail("unterminated string")
            ch = self.s[self.p]
            if ch == q:
                self.p += 1
                return "".join(b)
            if ch == "\\":
                nxt = self.peek(1)
                if self.p + 1 < self.n and (nxt == "\n" or nxt == "\x0c"):
                    self.p += 2  # an escaped line break continues the string
                elif self.p + 2 < self.n and nxt == "\r":
                    self.p += 3 if self.peek(2) == "\n" else 2
                else:
                    b.append(self.read_escape())
            else:
                b.append(ch)
                self.p += 1

    def read_escape(self):
        """'\\' then up to 6 hex digits and one optional whitespace, or any other char as itself."""
        self.p += 1  # '\'
        if self.p >= self.n:
            return REPLACEMENT
        if not is_hex(self.s[self.p]):
            ch = self.s[self.p]
            self.p += 1
            return ch
        digits = 0
        while digits < 6 and self.p + digits < self.n and is_hex(self.s[self.p + digits]):
            digits += 1
        code = int(self.s[self.p:self.p + digits], 16)
        self.p += digits
        if self.p < self.n and self.s[self.p] in CSS_SPACE:
            self.p += 2 if self.s[self.p] == "\r" and self.peek(1) == "\n" else 1
        if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
            return REPLACEMENT
        return chr(code)


def _parse_int32(s):
    i = 1 if s and s[0] in "+-" else 0
    rest = s[i:]
    if not rest or not rest.isascii() or not rest.isdigit():
        return None
    digits = rest.lstrip("0")
    if len(digits) > 10:
        return None
    v = int(digits) if digits else 0
    if s[0] == "-":
        v = -v
    return v if -2147483648 <= v <= 2147483647 else None


def attribute_selector(name, op, value, flag):
    """op: "" (exists), "=", "~", "|", "^", "$", "*" """
    code = {"": OP_EXISTS, "=": OP_EQUALS, "~": OP_INCLUDES, "|": OP_DASH, "^": OP_PREFIX, "$": OP_SUFFIX}.get(
        op, OP_SUBSTRING)
    fold = (lower(name) in CASE_INSENSITIVE_VALUES) if flag is None else flag
    return (S_ATTR, ascii_lower(name), code, value, False if op == "" else fold)


def compile_css(selector):
    """The selector list of a CSS selector; raises CssSelectorError on an invalid one."""
    c = _Compiler(selector)
    lst = c.parse_list(False, True)
    if c.p < len(selector):
        c.fail(f"unexpected '{selector[c.p]}'")
    return lst


# ----- sibling links and positions, built once per document by the first selector that needs them -----

class CssTree:
    __slots__ = ("d", "previous", "next", "position", "from_end", "types")

    def __init__(self, d):
        self.d = d
        count = len(d.keys)
        parents = d.parents
        previous = [-1] * count
        nxt = [-1] * count
        position = [0] * count
        from_end = [0] * count
        # per parent (slot = parent index + 1, slot 0: the top level): its last element child so far and their number
        last_child = [-1] * (count + 1)
        children = [0] * (count + 1)
        for i in range(count):
            if element_at(d, i):
                slot = parents[i] + 1
                prev = last_child[slot]
                previous[i] = prev
                if prev >= 0:
                    nxt[prev] = i
                last_child[slot] = i
                children[slot] += 1
                position[i] = children[slot]
        for i in range(count):
            if position[i] > 0:
                from_end[i] = children[parents[i] + 1] - position[i] + 1
        self.previous = previous
        self.next = nxt
        self.position = position
        self.from_end = from_end
        self.types = None

    def type_positions(self):
        """(position, from_end) among the siblings of the same type."""
        if self.types is not None:
            return self.types
        d = self.d
        count = len(d.keys)
        pos = [0] * count
        fin = [0] * count
        seen = {}
        keys = [None] * count
        for i in range(count):
            if self.position[i] != 0:
                key = d.keys[i]
                k = (d.parents[i], key, d.customs[i] if key < 0 else None)
                keys[i] = k
                seen[k] = seen.get(k, 0) + 1
                pos[i] = seen[k]
        for i in range(count):
            if pos[i] > 0:
                fin[i] = seen[keys[i]] - pos[i] + 1
        self.types = (pos, fin)
        return self.types


# ----- matching -----

class CssMatcher:
    """A compiled selector list and its matching."""

    __slots__ = ("selector", "list", "names")

    def __init__(self, selector):
        self.selector = selector
        self.list = compile_css(selector)
        # the name of the rightmost compound of each selector, checked on the arrays first
        names = []
        for item in self.list:
            last = item.c[-1]
            if last.key < 0 and last.name == "":
                names = None  # any element
                break
            names.append(last)
        self.names = names

    def scan(self, d, scope, start, end, first_only):
        out = []
        ends = d.ends
        keys = d.keys
        customs = d.customs
        lst = self.list
        names = self.names
        # one selector with a known tag name: one int compare per tag
        single = names[0].key if names is not None and len(names) == 1 and names[0].key >= 0 else None
        count = len(keys)
        i = start
        while i < count:
            if ends[i] > end:
                break
            if single is not None:
                if keys[i] != single:
                    i += 1
                    continue
            elif names is not None:
                k = keys[i]
                for cp in names:
                    if (k == cp.key) if cp.key >= 0 else (k < 0 and customs[i] == cp.name):
                        break
                else:
                    i += 1
                    continue
            elif not element_at(d, i):
                i += 1
                continue
            if matches_list(lst, d, i, scope, scope):
                out.append(i)
                if first_only:
                    break
            i += 1
        return out

    def matches(self, d, i):
        """element.matches(): :scope is the tag itself; False for comments."""
        return element_at(d, i) and matches_list(self.list, d, i, i, i)


def matches_list(lst, d, n, scope, anchor):
    """scope: the tag the query was called on (:scope, None for the document); anchor: the tag a relative selector
    starts from (:has(> a), or node.css("> li"); None: the document)."""
    for item in lst:
        if matches_complex(item, len(item.c) - 1, d, n, scope, anchor):
            return True
    return False


def matches_complex(cx, i, d, n, scope, anchor):
    """n matches compound i and the part to the left of it matches the tags the combinators lead to."""
    if not matches_compound(cx.c[i], d, n, scope, anchor):
        return False
    if i == 0:
        return cx.lead == COMB_NONE or related(cx.lead, d, anchor, n)
    k = cx.k[i - 1]
    if k == COMB_CHILD:
        p = d.parents[n]
        return p >= 0 and matches_complex(cx, i - 1, d, p, scope, anchor)
    if k == COMB_DESCENDANT:
        parents = d.parents
        p = parents[n]
        while p >= 0:
            if matches_complex(cx, i - 1, d, p, scope, anchor):
                return True
            p = parents[p]
        return False
    if k == COMB_ADJACENT:
        p = d.css_tree().previous[n]
        return p >= 0 and matches_complex(cx, i - 1, d, p, scope, anchor)
    if k == COMB_SIBLING:
        prev = d.css_tree().previous
        p = prev[n]
        while p >= 0:
            if matches_complex(cx, i - 1, d, p, scope, anchor):
                return True
            p = prev[p]
        return False
    return False


def matches_compound(cp, d, n, scope, anchor):
    if cp.key >= 0:
        if d.keys[n] != cp.key:
            return False
    elif cp.name:
        if d.keys[n] >= 0 or d.customs[n] != cp.name:
            return False
    elif not element_at(d, n):
        return False
    for s in cp.s:
        if not matches_simple(s, d, n, scope, anchor):
            return False
    return True


def related(c, d, anchor, n):
    """n stands in the combinator relation to anchor (None: the document)."""
    if c == COMB_CHILD:
        return d.parents[n] < 0 if anchor is None else d.parents[n] == anchor
    if c == COMB_DESCENDANT:
        if anchor is None:
            return True
        parents = d.parents
        p = parents[n]
        while p >= 0:
            if p == anchor:
                return True
            p = parents[p]
        return False
    if c == COMB_ADJACENT:
        return anchor is not None and d.css_tree().previous[n] == anchor
    if c == COMB_SIBLING:
        return anchor is not None and anchor < n and d.parents[anchor] == d.parents[n] and element_at(d, anchor)
    return False


def has_match(relative, d, anchor, scope):
    """:has(): whether a tag in the anchor's scope matches the relative selector."""
    ends = d.ends
    count = len(ends)
    anchor_end = ends[anchor]
    for item in relative:
        last = len(item.c) - 1
        i = anchor + 1
        if item.lead == COMB_DESCENDANT or item.lead == COMB_CHILD:
            # the tags inside the anchor
            while i < count and ends[i] <= anchor_end:
                if element_at(d, i) and matches_complex(item, last, d, i, scope, anchor):
                    return True
                i += 1
        else:
            # the following siblings and the tags inside them, up to the end of the parent
            while i < count and ends[i] <= anchor_end:
                i += 1
            parent = d.parents[anchor]
            end = ends[parent] if parent >= 0 else float("inf")
            while i < count and ends[i] <= end:
                if element_at(d, i) and matches_complex(item, last, d, i, scope, anchor):
                    return True
                i += 1
    return False


def contains_word(lst, word, fold):
    """Whether a whitespace-separated list contains word."""
    if not word or len(lst) < len(word):
        return False
    if not fold and CSS_SPACE_RE.search(lst) is None:
        return lst == word
    for w in CSS_SPACE_RE.split(lst):
        if w and (equal_fold(w, word) if fold else w == word):
            return True
    return False


def _decoded(raw):
    return dec.decode(raw, True) if "&" in raw or "\r" in raw else raw


def matches_simple(s, d, n, scope, anchor):
    kind = s[0]
    if kind == S_ID:
        return at.attr_equals(at.attr_raw(d, n, "id"), s[1])
    if kind == S_CLASS:
        raw = at.attr_raw(d, n, "class")
        return raw != "" and contains_word(_decoded(raw), s[1], False)
    if kind == S_ATTR:
        return matches_attribute(d, n, s[1], s[2], s[3], s[4])
    if kind == S_NTH:
        return matches_nth(d, n, s[1], s[2], s[3], s[4], s[5], scope, anchor)
    if kind == S_NOT:
        return not matches_list(s[1], d, n, scope, anchor)
    if kind == S_IS:
        return matches_list(s[1], d, n, scope, anchor)
    if kind == S_HAS:
        return has_match(s[1], d, n, scope)
    if kind == S_CONTAINS:
        return s[1] in d.text_of(n)
    return matches_pseudo(s[1], d, n, scope)


def _eq(a, b, fold):
    return equal_fold(a, b) if fold else a == b


def matches_attribute(d, n, name, op, v, fold):
    raw = at.attr_raw(d, n, name)
    if not raw:
        # missing or empty: only these can match an empty value
        if op == OP_EXISTS:
            return at.has_attribute(d, n, name)
        if op == OP_EQUALS or op == OP_DASH:
            return v == "" and at.has_attribute(d, n, name)
        return False
    if op == OP_EXISTS:
        return True
    actual = _decoded(raw)
    vl = len(v)
    if op == OP_EQUALS:
        return _eq(actual, v, fold)
    if op == OP_INCLUDES:
        return CSS_SPACE_RE.search(v) is None and contains_word(actual, v, fold)
    if op == OP_DASH:
        return _eq(actual, v, fold) or (len(actual) > vl and actual[vl] == "-" and _eq(actual[:vl], v, fold))
    if op == OP_PREFIX:
        return v != "" and len(actual) >= vl and _eq(actual[:vl], v, fold)
    if op == OP_SUFFIX:
        return v != "" and len(actual) >= vl and _eq(actual[len(actual) - vl:], v, fold)
    if op == OP_SUBSTRING:
        if not v:
            return False
        if not fold:
            return v in actual
        if v.isascii():
            return ascii_lower(v) in ascii_lower(actual)
        return lower(v) in lower(actual)
    return False


def matches_nth(d, n, a, b, from_end, of_type, of, scope, anchor):
    """:nth-child(An+B [of S]) and its relatives; :first-child is :nth-child(1), and so on."""
    t = d.css_tree()
    if of is not None:
        if not matches_list(of, d, n, scope, anchor):
            return False
        position = 1
        links = t.next if from_end else t.previous
        sib = links[n]
        while sib >= 0:
            if matches_list(of, d, sib, scope, anchor):
                position += 1
            sib = links[sib]
    elif of_type:
        tp = t.type_positions()
        position = tp[1][n] if from_end else tp[0][n]
    else:
        position = t.from_end[n] if from_end else t.position[n]
    if position <= 0:
        return False  # not an element
    # position = a*n + b for some n >= 0
    if a == 0:
        return position == b
    diff = position - b
    return diff % a == 0 and diff // a >= 0


def _root(d, n):
    """the first top-level element: <html> on a page"""
    return d.parents[n] < 0 and d.css_tree().previous[n] < 0


def _empty_element(d, n):
    """No child elements and no text; comments are allowed."""
    inner_start = d.inner_starts[n]
    inner_end = d.inner_ends[n]
    if inner_start <= 0 or inner_end <= 0:
        return True  # a void or unclosed tag has no content
    position = inner_start
    ends = d.ends
    starts = d.starts
    end = ends[n]
    i = n + 1
    count = len(ends)
    while i < count and ends[i] <= end:
        if element_at(d, i) or starts[i] > position:
            return False  # an element, or text before a comment
        if ends[i] + 1 > position:
            position = ends[i] + 1
        i += 1
    return position > inner_end


def matches_pseudo(kind, d, n, scope):
    name = d.name_of(n)
    if kind == PSEUDO_ROOT:
        return _root(d, n)
    if kind == PSEUDO_SCOPE:
        return _root(d, n) if scope is None else n == scope
    if kind == PSEUDO_EMPTY:
        return _empty_element(d, n)
    if kind == PSEUDO_LINK:
        return (name == "a" or name == "area") and at.has_attribute(d, n, "href")
    if kind == PSEUDO_CHECKED:
        return (name == "input" and at.has_attribute(d, n, "checked")) or \
            (name == "option" and at.has_attribute(d, n, "selected"))
    if kind == PSEUDO_DISABLED:
        return name in FORM_CONTROLS and at.has_attribute(d, n, "disabled")
    if kind == PSEUDO_ENABLED:
        return name in FORM_CONTROLS and not at.has_attribute(d, n, "disabled")
    if kind == PSEUDO_REQUIRED:
        return name in INPUTS and at.has_attribute(d, n, "required")
    if kind == PSEUDO_OPTIONAL:
        return name in INPUTS and not at.has_attribute(d, n, "required")
    return False
