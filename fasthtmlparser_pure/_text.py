"""Visible text: tags, comments and invisible content removed, whitespace collapsed, entities decoded; and the text
with the visible line structure kept (Node.text_line_breaks)."""

import re

from . import _decode as dec
from ._chars import ASCII_SPACE, HAS_SPACE, SPACE_RUNS, ascii_lower
from ._parser import KEYS, NAMES, find_comment_end, find_raw_text_end, lookup_tag

INVISIBLE = {
    "style": "</style", "script": "</script", "noembed": "</noembed", "noscript": "</noscript",
    "template": "</template", "noframes": "</noframes",
}
# '<' that starts markup: before an ASCII letter, '/', '!' or '?'
MARKUP = re.compile(r"<[a-zA-Z/!?]")


def skip_markup(s, i):
    """s[i] is '<'. Returns the index of the last char of what is not text there: a comment, a tag, or a tag together
    with invisible content (script, style, ...) up to its end tag. -1: an unterminated tag."""
    n = len(s)
    if i + 4 <= n and s[i + 1] == "!" and s[i + 2] == "-" and s[i + 3] == "-":
        return find_comment_end(s, i + 4, n)
    tag_end = s.find(">", i + 1)
    if tag_end < 0:
        return -1
    end_tag = invisible_content_end(s, i + 1, tag_end)
    if end_tag is not None:
        return find_raw_text_end(s, tag_end + 1, end_tag, n) - 1
    return tag_end


def invisible_content_end(s, start, to):
    """For the inside of an opening tag s[start:to], the end tag ("</script") when the element's content is never
    visible text (script, style, noscript, template, noframes, noembed), otherwise None."""
    if start >= to:
        return None
    first = s[start]
    if first not in "sStTnN":
        return None
    n = 1
    while start + n < to and n <= 8:
        c = s[start + n]
        if not (("a" <= c <= "z") or ("A" <= c <= "Z")):
            break
        n += 1
    if start + n < to:
        c = s[start + n]
        if c != "/" and c not in ASCII_SPACE:
            return None
    end = INVISIBLE.get(ascii_lower(s[start:start + n]))
    if end is None:
        return None
    e = to
    while e > start and s[e - 1] in ASCII_SPACE:
        e -= 1
    if s[e - 1] == "/":
        return None  # <script src="x" />: no content
    return end


def text_runs(s):
    """The text runs of s with tags, comments and invisible content removed, whitespace not yet collapsed."""
    search = MARKUP.search
    m = search(s)
    if m is None:
        return s
    out = []
    start = 0
    while m is not None:
        lt = m.start()
        if lt > start:
            out.append(s[start:lt])
        last = skip_markup(s, lt)
        if last < 0:
            return "".join(out)  # an unterminated tag: the rest is markup
        start = last + 1
        m = search(s, start)
    if start < len(s):
        out.append(s[start:])
    return "".join(out)


def collapse(s):
    """Whitespace runs as one ' ', trimmed."""
    if HAS_SPACE.search(s) is None:
        return s
    t = SPACE_RUNS.sub(" ", s)
    if not t:
        return t
    a = 1 if t[0] == " " else 0
    z = len(t) - (1 if t[-1] == " " else 0)
    return t if a == 0 and z == len(t) else t[a:z]


def clear_tags(s):
    """The visible text of an HTML fragment."""
    if not s:
        return ""
    text = collapse(text_runs(s))
    return dec.decode(text, False) if "&" in text else text


def text_equals(value, expected, d, i):
    """Whether the visible text of the body (value) of the tag i equals expected."""
    if not value or not expected:
        return False
    # whitespace is collapsed even where text keeps it (a tag without children), as the .NET version compares
    text = collapse(text_runs(value))
    amp = text.find("&")
    if amp < 0:
        return text == expected
    # Text with entities must be decoded before comparing: the full text, when the part before it matches.
    head = text[:amp].rstrip(" ")
    return expected.startswith(head) and d.text_of(i) == expected


# ----- text with line breaks -----

BREAK_SPACE = 1  # table cell: word separator
BREAK_LINE = 2  # <br> and block elements (div, li, tr, ...): new line
BREAK_PARAGRAPH = 3  # p, h1-h6: new paragraph (blank line)
BREAK_SKIP_CONTENT = 4  # script, style, noscript: contents are not text
# inline tags (b, span, a, ...) are removed, words are not split

BR = KEYS["br"]
KINDS = [0] * len(NAMES)
for _t in "p h1 h2 h3 h4 h5 h6".split():
    KINDS[KEYS[_t]] = BREAK_PARAGRAPH
for _t in ("br div li ul ol dl dt dd table caption tr thead tbody tfoot section article aside nav header footer main "
           "address blockquote pre hr figure figcaption form fieldset legend details summary dialog option html head "
           "body title hgroup search menu center dir listing xmp plaintext multicol frameset marquee").split():
    KINDS[KEYS[_t]] = BREAK_LINE
KINDS[KEYS["td"]] = BREAK_SPACE
KINDS[KEYS["th"]] = BREAK_SPACE
for _t in "script style noscript template noframes noembed".split():
    KINDS[KEYS[_t]] = BREAK_SKIP_CONTENT

PLAIN = re.compile(r"[^&<]*")
TAG_NAME_END = re.compile(r"[\t\r\n\f /]")


class _Lines:
    __slots__ = ("out", "pending_breaks", "pending_space", "started")

    def __init__(self):
        self.out = []
        self.pending_breaks = 0  # new lines requested since the last text char
        self.pending_space = False
        self.started = False

    def chars(self, s):
        """Writes text, collapsing whitespace and turning requested breaks into trimmed lines."""
        if HAS_SPACE.search(s) is None:
            self.text(s)
            return
        pos = 0
        n = len(s)
        for m in SPACE_RUNS.finditer(s):
            i = m.start()
            if i > pos:
                self.text(s[pos:i])
            self.pending_space = True
            pos = m.end()
        if pos < n:
            self.text(s[pos:])

    def text(self, s):
        if self.started:
            # nothing before the first text char: leading breaks and spaces are trimmed
            if self.pending_breaks > 0:
                self.out.append("\n\n" if self.pending_breaks >= 2 else "\n")  # at most one blank line
            elif self.pending_space:
                self.out.append(" ")
        self.pending_breaks = 0
        self.pending_space = False
        if s:
            self.out.append(s)
            self.started = True

    def brk(self, kind, known):
        if kind == BREAK_LINE:
            if known == BR:
                self.pending_breaks += 1  # every <br> counts
            elif self.pending_breaks < 1:
                self.pending_breaks = 1
        elif self.pending_breaks < 2:
            self.pending_breaks = 2


def clear_tags_line_breaks(value):
    """The text of an HTML fragment with the visible line structure kept. Lines are joined with "\\n"."""
    if not value:
        return ""
    value = dec.normalize_newlines(value)  # values are read as a browser reads the page
    w = _Lines()
    n = len(value)
    plain = PLAIN.match
    i = 0
    while i < n:
        c = value[i]
        if c == "&":
            # '&': decoded chars go through the same whitespace/text logic (&nbsp; collapses, &lt; is plain text)
            decoded, consumed = dec.decode_reference(value, i + 1)
            if decoded is not None:
                w.chars(decoded)
                i += 1 + consumed
            else:
                w.chars("&")
                i += 1
            continue

        if c != "<" or not (i + 1 < n and (value[i + 1].isascii() and (value[i + 1].isalpha() or value[i + 1] in "/!?"))):
            # plain text up to the next '&' or '<'
            j = plain(value, i + 1).end()
            w.chars(value[i:j])
            i = j
            continue

        if i + 4 <= n and value[i + 1] == "!" and value[i + 2] == "-" and value[i + 3] == "-":
            e = value.find("-->", i + 4)
            if e < 0:
                break
            i = e + 3
            continue

        close_tag = value.find(">", i)
        if close_tag < 0:
            break  # unterminated tag: the rest is markup
        tag = value[i + 1:close_tag]
        i = close_tag + 1

        closing = tag[:1] == "/"
        name = tag[1:] if closing else tag
        m = TAG_NAME_END.search(name)
        if m is not None:
            name = name[:m.start()]
        known = lookup_tag(name)
        if known < 0:
            continue  # unknown tag: just removed

        kind = KINDS[known]
        if kind == BREAK_LINE or kind == BREAK_PARAGRAPH:
            w.brk(kind, known)
        elif kind == BREAK_SPACE:
            w.chars(" ")
        elif kind == BREAK_SKIP_CONTENT:
            if not closing and not tag.rstrip(ASCII_SPACE).endswith("/"):
                # jump to the closing tag; it is removed on the next iteration
                e = _index_fold(value, "</" + NAMES[known], i)
                i = n if e < 0 else e
    return "".join(w.out)


def _index_fold(s, sub, start):
    m = re.compile(re.escape(sub), re.IGNORECASE | re.ASCII).search(s, start)
    return -1 if m is None else m.start()
