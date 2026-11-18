"""The parser: a page is scanned once in fast mode, where tags are matched to their end tags and nothing else.

Well-formed markup needs nothing more. Where the HTML5 optional-end-tag rules matter (<li>one<li>two</ul>), tags are
left open inside an element: when its end tag (or the end of the document) finds them on the stack and one of them is
a rule target (li, p, td, tr, option, ...), only that part is scanned again in rules mode, from the outermost such tag
up to that end tag. Everything before it was parsed right and is kept.

Port of ParsingCore.cs from z3l.FastHtmlParser; positions are char indexes into the source.
"""

import re

from ._chars import ASCII_SPACE, SPACE, ascii_lower, lower
from ._tables import TAG_NAMES

NAMES = TAG_NAMES
KEYS = {n: k for k, n in enumerate(NAMES)}
COMMENT = KEYS["!--"]
DOCTYPE = KEYS["!doctype"]
XML_DECLARATION = KEYS["?xml"]
P = KEYS["p"]
BOM = chr(0xFEFF)

# Nesting deeper than this is flattened, as browsers do (Blink: 512).
MAX_DEPTH = 512
# The parts scanned again with the optional-end-tag rules may add up to this many times the source length.
REPAIR_BUDGET_FACTOR = 8

# "</script", "</style", ... by tag key, for the elements whose content is raw text (HTML5 RAWTEXT / RCDATA).
RAW_TEXT_ENDS = [None] * len(NAMES)
for _n in ("script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes"):
    RAW_TEXT_ENDS[KEYS[_n]] = "</" + _n

_END_PATTERNS = {}


def end_pattern(end_tag):
    """A case-insensitive (ASCII) search for an end tag such as "</script"."""
    p = _END_PATTERNS.get(end_tag)
    if p is None:
        p = re.compile(re.escape(end_tag), re.IGNORECASE | re.ASCII)
        _END_PATTERNS[end_tag] = p
    return p


# HTML5 void elements (incl. obsolete ones the parsing rules also treat as void) and declarations.
VOID_NAMES = ("area base br col embed hr img input link meta source track wbr basefont bgsound frame keygen param "
              "isindex !doctype ?xml").split()
# Elements that limit the scope of the rules (HTML5 "has an element in scope").
SCOPE_STOPS = "html body table caption td th template object applet marquee button".split()
# <p> is ended by the start of a block element (HTML5 "close a p element").
CLOSES_P_NAMES = ("address article aside blockquote center details dialog dir div dl fieldset figcaption figure "
                  "footer form h1 h2 h3 h4 h5 h6 header hgroup hr listing main menu nav ol p pre search section "
                  "summary table ul li dd dt plaintext xmp").split()

F_VOID = 1
F_CLOSES_P = 2
F_RULE = 4
F_RAW_TEXT = 8


class Rule:
    """t (targets) and s (stops) as flags by tag key, k: the target keys."""

    __slots__ = ("t", "s", "k")

    def __init__(self, targets, *stops):
        t = [False] * len(NAMES)
        s = [False] * len(NAMES)
        for n in targets:
            t[KEYS[n]] = True
        for lst in stops:
            for n in lst:
                s[KEYS[n]] = True
        self.t = t
        self.s = s
        self.k = tuple(KEYS[n] for n in targets)


P_RULE = Rule(["p"], SCOPE_STOPS)

RULES = [None] * len(NAMES)
RULES[KEYS["li"]] = Rule(["li"], SCOPE_STOPS, ["ul", "ol", "menu"])
RULES[KEYS["dt"]] = RULES[KEYS["dd"]] = Rule(["dt", "dd"], SCOPE_STOPS, ["dl"])
_table_stops = ["html", "body", "template", "table"]
RULES[KEYS["td"]] = RULES[KEYS["th"]] = Rule(["td", "th"], _table_stops, ["tr", "tbody", "thead", "tfoot"])
RULES[KEYS["tr"]] = Rule(["tr", "td", "th"], _table_stops, ["tbody", "thead", "tfoot"])
_section = Rule(["tbody", "thead", "tfoot", "tr", "td", "th", "caption"], _table_stops)
RULES[KEYS["tbody"]] = RULES[KEYS["thead"]] = RULES[KEYS["tfoot"]] = _section
_select_stops = ["html", "body", "template", "select", "datalist"]
RULES[KEYS["option"]] = Rule(["option"], _select_stops, ["optgroup"])
RULES[KEYS["optgroup"]] = Rule(["option", "optgroup"], _select_stops)

FLAGS = [0] * len(NAMES)
for _n in VOID_NAMES:
    FLAGS[KEYS[_n]] |= F_VOID
for _n in CLOSES_P_NAMES:
    FLAGS[KEYS[_n]] |= F_CLOSES_P
for _k, _e in enumerate(RAW_TEXT_ENDS):
    if _e:
        FLAGS[_k] |= F_RAW_TEXT
for _k, _r in enumerate(RULES):
    if _r:
        FLAGS[_k] |= F_RULE

IS_RULE_TARGET = [False] * len(NAMES)
for _r in RULES:
    if _r:
        for _k in _r.k:
            IS_RULE_TARGET[_k] = True

# Inside a tag only '<', '=' and '>' matter; a quoted value after '=' may contain '<' and '>' (data-x="<p>").
# Nothing follows it in a match, so the greedy quantifiers never backtrack and act as possessive ones.
TAG_BODY = re.compile(r"""(?:[^<>=]+|=[\t-\r ]*(?:"[^"]*"|'[^']*')?)*""")
# A tag name ends at whitespace or '/' (<br/> is "br", as <br /> is). On the way to the end of the tag it is read up
# to '<', '=' or '>' too (TAG_BODY reads what follows); when it runs into '=' it is read char by char instead.
NAME_PART = re.compile(r"[^\t-\r /<=>]*")
# NAME_PART and TAG_BODY in one match (the fast scan)
OPEN_TAG = re.compile(r"""([^\t-\r /<=>]*)(?:[^<>=]+|=[\t-\r ]*(?:"[^"]*"|'[^']*')?)*""")


def lookup_tag(name):
    """Finds a tag name case-insensitively (ASCII); -1 for a name that is not a known tag."""
    if not name or len(name) > 32:
        return -1
    k = KEYS.get(name)
    if k is None:
        k = KEYS.get(ascii_lower(name), -1)
    return k


def is_raw_text(key):
    """Whether the content of the tag is raw text (script, style, textarea, title, ...)."""
    return key >= 0 and RAW_TEXT_ENDS[key] is not None


def is_markup_start(s, i, limit):
    """As in HTML5, '<' starts markup only before an ASCII letter, '/', '!' or '?'."""
    if i + 1 >= limit:
        return False
    c = s[i + 1]
    return ("a" <= c <= "z") or ("A" <= c <= "Z") or c == "/" or c == "!" or c == "?"


def find_raw_text_end(s, start, end_tag, limit):
    """The position of the '<' of the end tag (e.g. "</script") in s[start:limit], or limit if there is none."""
    n = len(end_tag)
    search = end_pattern(end_tag).search
    while start < limit:
        m = search(s, start)
        if m is None:
            break
        pos = m.start()
        if pos + n > limit:
            break
        after = pos + n
        # "</script>", "</script >", "</script/>" end it; "</scripts" does not.
        if after >= limit:
            return pos
        c = s[after]
        if c == ">" or c == "/" or c in ASCII_SPACE:
            return pos
        start = after
    return limit


def find_comment_end(s, content_start, limit):
    """The position of the '>' that ends a comment whose content starts at content_start (in s[:limit]).

    As in HTML5: "<!-->" and "<!--->" are empty comments, "-->" or "--!>" end a comment, otherwise it runs to the end.
    """
    if content_start < limit and s[content_start] == ">":
        return content_start
    if content_start + 1 < limit and s[content_start] == "-" and s[content_start + 1] == ">":
        return content_start + 1
    start = content_start
    while start < limit:
        dashes = s.find("--", start)
        if dashes < 0 or dashes + 2 > limit:
            break
        pos = dashes + 2
        if pos < limit and s[pos] == ">":
            return pos
        if pos + 1 < limit and s[pos] == "!" and s[pos + 1] == ">":
            return pos + 1
        start = dashes + 1
    return limit - 1


def is_xml(src):
    """An XML document (RSS, Atom, sitemap, XHTML) is parsed by XML rules: elements end only with an end tag or "/>"."""
    i = 0
    n = len(src)
    while i < n and (src[i] in ASCII_SPACE or src[i] == BOM):
        i += 1
    return n - i >= 5 and src[i] == "<" and src[i + 1] == "?" and ascii_lower(src[i:i + 5]) == "<?xml"


def parse_tags(src):
    """Parses src into the flat tag list: the tags in document order as parallel lists
    (keys, custom names, parents, depths, starts, ends, inner starts, inner ends)."""
    if "<" not in src:
        return [], [], [], [], [], [], [], []
    st = _Parser(src)
    st.scan_fast()
    return st.finish()


class _Parser:
    def __init__(self, src):
        self.src = src
        self.is_xml = is_xml(src)
        self.budget = REPAIR_BUDGET_FACTOR * len(src)  # chars that may still be scanned again by repair
        self.keys = []  # tag key, or -1 for a custom name
        self.customs = []  # the lower-case name of a tag that is not a known one, "" for the others
        self.parents = []  # the index of the enclosing tag, -1 at the top level
        self.starts = []  # the opening '<'
        self.ends = []  # the last '>': of the end tag, or of the opening tag
        self.inner_starts = []  # right after the opening tag
        self.inner_ends = []  # the last char before the end tag; 0 when the tag is not closed
        self.depths = []
        self.prev_open = []  # the open tags as a linked list in document order
        self.self_closed = []  # <x/>: stays in the open list (a later </x> ends it), but nests nothing
        self.stack = []  # indexes of the open tags that contain the following ones
        self.open_by_key = [0] * len(NAMES)  # per tag, how many are on the stack: kept only in rules mode
        self.open_in_list = [0] * len(NAMES)  # per tag, how many are in the open list
        self.custom_open = {}  # custom names (lower case): how many such tags are in the open list
        self.count = 0
        self.stack_top = 0
        self.last_open = -1
        self.open_p = 0  # <p> tags on the stack, kept in both modes
        self.tag_started = False  # scan results
        self.tag_start = 0

    def finish(self):
        """The end of the document ends every tag still open (a truncated page keeps its text), right before an
        unfinished tag if the source stops inside one."""
        region_end = self.tag_start - 1 if self.tag_started else len(self.src)
        if not self.is_xml:
            s = self.first_misnested(0, self.stack_top)
            if s >= 0:
                self.repair(s, region_end)
        self.truncate_open_list(0, region_end - 1)
        count = self.count
        out = [self.keys, self.customs, self.parents, self.depths, self.starts, self.ends, self.inner_starts,
               self.inner_ends]
        for a in out:
            del a[count:]
        inner_starts = self.inner_starts
        for k, e in enumerate(self.inner_ends):
            if e == 0:
                inner_starts[k] = 0  # not closed: no body
        return out

    def scan_fast(self):
        """The first scan of the whole page in fast mode: what scan(False, 0, size) does, with the common cases inline
        and the state in local variables. Whatever the inline code does not handle (comments, custom names, the
        optional-end-tag rules, a misnested end tag, an unfinished tag) is handed to the general code with the state
        written back."""
        src = self.src
        to = len(src)
        keys = self.keys
        customs = self.customs
        parents = self.parents
        depths = self.depths
        starts = self.starts
        ends = self.ends
        inner_starts = self.inner_starts
        inner_ends = self.inner_ends
        prev_open = self.prev_open
        self_closed = self.self_closed
        stack = self.stack
        open_in_list = self.open_in_list
        xml = self.is_xml
        tag_body = TAG_BODY.match
        open_tag = OPEN_TAG.match
        find = src.find
        get_key = KEYS.get
        flags_of = FLAGS
        rules_of = RULES
        raw_ends = RAW_TEXT_ENDS
        count = 0
        stack_top = 0
        last_open = -1
        open_p = 0
        i = 0
        while i < to:
            lt = find("<", i)
            if lt < 0:
                break
            i = lt
            if i + 1 >= to:
                break
            c = src[i + 1]
            tag_start = i + 1
            if c == "/":
                p = tag_body(src, tag_start).end()
                if p < to and src[p] == ">":
                    key = get_key(src[tag_start + 1:p])
                    j = last_open
                    if key is not None and j >= 0 and keys[j] == key and stack_top > 0 and stack[stack_top - 1] == j:
                        # the end tag of the innermost open tag
                        inner_ends[j] = tag_start - 2
                        ends[j] = p
                        stack_top -= 1
                        if key == P:
                            open_p -= 1
                        open_in_list[key] -= 1
                        last_open = prev_open[j]
                        i = p + 1
                        continue
            else:
                if not (("a" <= c <= "z") or ("A" <= c <= "Z") or c == "!" or c == "?"):
                    i += 1
                    continue
                if c != "!":
                    # the name and the rest of the tag in one match
                    m = open_tag(src, tag_start)
                    p = m.end()
                    name = m.group(1)
                    if src.startswith("=", m.end(1)):
                        name = None
                    if name is not None and p < to and src[p] == ">" and src[p - 1] < "\x80":
                        key = get_key(name)
                        if key is None and len(name) <= 32:
                            key = get_key(ascii_lower(name))
                        if key is not None:
                            flags = 0 if xml else flags_of[key]
                            if (flags & F_RULE == 0 or stack_top == 0 or keys[stack[stack_top - 1]] < 0
                                    or not rules_of[key].t[keys[stack[stack_top - 1]]]) \
                                    and (open_p == 0 or flags & F_CLOSES_P == 0):
                                # a known tag that ends no open tag
                                if stack_top > 0:
                                    parent = stack[stack_top - 1]
                                    depth = depths[parent] + 1
                                else:
                                    parent = -1
                                    depth = 0
                                customs.append("")
                                parents.append(parent)
                                starts.append(i)
                                ends.append(p)
                                inner_starts.append(p + 1)
                                inner_ends.append(0)
                                keys.append(key)
                                depths.append(depth)
                                if flags & F_VOID:
                                    prev_open.append(-1)
                                    self_closed.append(False)
                                    count += 1
                                    i = p + 1
                                    continue
                                last = src[p - 1]
                                self_closing = last == "/" or (last <= " " and self._self_closing(tag_start, p))
                                self_closed.append(self_closing)
                                prev_open.append(last_open)
                                last_open = count
                                open_in_list[key] += 1
                                count += 1
                                i = p + 1
                                if self_closing:
                                    continue
                                if stack_top < MAX_DEPTH:
                                    if stack_top < len(stack):
                                        stack[stack_top] = count - 1
                                    else:
                                        stack.append(count - 1)
                                    stack_top += 1
                                    if key == P:
                                        open_p += 1
                                # <script>, <style>, ...: continue right before the matching end tag
                                if flags & F_RAW_TEXT:
                                    i = find_raw_text_end(src, p + 1, raw_ends[key], to)
                                continue
            # anything else: the general code, with the state written back
            self.count = count
            self.stack_top = stack_top
            self.last_open = last_open
            self.open_p = open_p
            stop = self.scan_one(i, to)
            count = self.count
            stack_top = self.stack_top
            last_open = self.last_open
            open_p = self.open_p
            if stop < 0:
                self.tag_started = True
                self.tag_start = i + 1
                return
            i = stop
        self.count = count
        self.stack_top = stack_top
        self.last_open = last_open
        self.open_p = open_p
        self.tag_started = False

    def scan(self, rules, start, to):
        """Parses src[start:to] (start is outside a tag), adding tags at count. Tags still open at to stay on the
        stack."""
        src = self.src
        tag_started = False
        tag_start = 0
        i = start
        while i < to:
            # Outside a tag only '<' matters: jump to it.
            lt = src.find("<", i, to)
            if lt < 0:
                break
            i = lt
            if i + 1 >= to:
                break
            c = src[i + 1]
            tag_start = i + 1
            if c == "/":
                # an end tag; inside a tag only '<', '=' and '>' matter
                p = TAG_BODY.match(src, tag_start).end()
                if p >= to:
                    p = self.scan_tag_slowly(tag_start, to)
                if p < 0:
                    tag_started = True
                    break
                if src[p] == "<":
                    # An unfinished tag: start over at this '<' (it may also start a comment).
                    i = p
                    continue
                self.end_tag(rules, tag_start, p)
                i = p + 1
                continue

            # As in HTML5, '<' starts markup only before a letter, '/', '!' or '?': "a < b" is text.
            if not (("a" <= c <= "z") or ("A" <= c <= "Z") or c == "!" or c == "?"):
                i += 1
                continue
            if c == "!" and i + 4 <= to and src[i + 2] == "-" and src[i + 3] == "-":
                # <!-- ... -->: one "!--" tag; its content is not markup, it does not nest anything.
                i = self.comment(i, to) + 1
                continue

            m = NAME_PART.match(src, tag_start)
            p = m.end()
            name = m.group()
            if p < len(src) and src[p] == "=":
                name = None  # the name runs into '=': it is read char by char
            # a tag without attributes ends right after its name
            if p < len(src) and src[p] != ">":
                p = TAG_BODY.match(src, p).end()
            if p >= to:
                p = self.scan_tag_slowly(tag_start, to)
            if p < 0:
                tag_started = True
                break
            if src[p] == "<":
                i = p
                continue
            i = self.start_tag(rules, tag_start, p, to, c, name) + 1
        self.tag_started = tag_started
        self.tag_start = tag_start

    def scan_one(self, i, to):
        """One step of scan(False, ...) at the '<' at i: the position to go on from, or -1 for a tag that does not
        end."""
        src = self.src
        c = src[i + 1]
        tag_start = i + 1
        if c == "/":
            p = TAG_BODY.match(src, tag_start).end()
            if p >= to:
                p = self.scan_tag_slowly(tag_start, to)
            if p < 0:
                return -1
            if src[p] == "<":
                return p
            self.end_tag(False, tag_start, p)
            return p + 1
        if not (("a" <= c <= "z") or ("A" <= c <= "Z") or c == "!" or c == "?"):
            return i + 1
        if c == "!" and i + 4 <= to and src[i + 2] == "-" and src[i + 3] == "-":
            return self.comment(i, to) + 1
        m = NAME_PART.match(src, tag_start)
        p = m.end()
        name = m.group()
        if p < len(src) and src[p] == "=":
            name = None
        if p < len(src) and src[p] != ">":
            p = TAG_BODY.match(src, p).end()
        if p >= to:
            p = self.scan_tag_slowly(tag_start, to)
        if p < 0:
            return -1
        if src[p] == "<":
            return p
        return self.start_tag(False, tag_start, p, to, c, name) + 1

    def _self_closing(self, tag_start, gt):
        """Whether the opening tag that ends at gt (with whitespace before '>') ends with "/"."""
        src = self.src
        e = gt
        while e > tag_start and src[e - 1] in ASCII_SPACE:
            e -= 1
        return e > tag_start and src[e - 1] == "/"

    def scan_tag_slowly(self, i, to):
        """The inside of a tag from i, as the fast scan reads it but within to: the position of the '<' or '>' that
        ends it, or -1 when the tag does not end before to."""
        src = self.src
        while i < to:
            c = src[i]
            if c == "<" or c == ">":
                return i
            if c == "=":
                q = i + 1
                while q < to and src[q] in ASCII_SPACE:
                    q += 1
                if q < to:
                    quote = src[q]
                    if quote == '"' or quote == "'":
                        close = src.find(quote, q + 1)
                        if 0 <= close < to:
                            i = close
            i += 1
        return -1

    def _add(self, key, custom, parent, start, end, depth, prev_open, self_closed):
        """Appends a tag; the lists always end at count (repair cuts them back)."""
        self.keys.append(key)
        self.customs.append(custom)
        self.parents.append(parent)
        self.starts.append(start)
        self.ends.append(end)
        self.inner_starts.append(end + 1)
        self.inner_ends.append(0)
        self.depths.append(depth)
        self.prev_open.append(prev_open)
        self.self_closed.append(self_closed)

    def comment(self, i, to):
        """Adds the comment that starts at i; returns the position of its '>'."""
        comment_end = find_comment_end(self.src, i + 4, to)
        parent = -1
        depth = 0
        if self.stack_top > 0:
            parent = self.stack[self.stack_top - 1]
            depth = self.depths[parent] + 1
        k = self.count
        self._add(COMMENT, "", parent, i, comment_end, depth, -1, False)
        self.count = k + 1
        return comment_end

    def end_tag(self, rules, tag_start, gt):
        """Matches the end tag src[tag_start-1 .. gt] to the innermost open tag with that name."""
        src = self.src
        name = src[tag_start + 1:gt]
        # Nearly always the name as it stands is a known tag name.
        key = KEYS.get(name)
        if key is None:
            name = name.strip(SPACE)
            if not name:
                return
            # Resolve the name once, then compare ints instead of a lookup per candidate.
            key = KEYS.get(name)
            if key is None:
                key = KEYS.get(ascii_lower(name), -1) if len(name) <= 32 else -1

        # Well-formed markup: the end tag closes the innermost open tag, which is on top of the stack. What
        # match_end and truncate_open_list do for it, without the loops.
        j = self.last_open
        if key >= 0 and j >= 0 and self.keys[j] == key and self.stack_top > 0 and self.stack[self.stack_top - 1] == j:
            self.inner_ends[j] = tag_start - 2
            self.ends[j] = gt
            self.stack_top -= 1
            if key == P:
                self.open_p -= 1
            if rules:
                self.open_by_key[key] -= 1
            self.open_in_list[key] -= 1
            self.last_open = self.prev_open[j]
            return
        # The rules may have ended the matched tag itself (<li><div><li>x</div>: the second <li> ends the first one
        # together with its <div>, as in a browser): the end tag is matched again, to what is open now.
        # Once: the part is not scanned a second time.
        if not self.match_end(rules, key, name, tag_start, gt, True):
            return
        self.match_end(rules, key, name, tag_start, gt, False)

    def match_end(self, rules, key, name, tag_start, gt, can_repair):
        """Ends the innermost open tag with the end tag's name. Returns True when it scanned a part again with the
        optional-end-tag rules instead, and the end tag is to be matched once more."""
        # A name that no open tag has is a stray end tag: it is skipped without scanning the open tags.
        closing = None
        if key >= 0:
            if self.open_in_list[key] <= 0:
                return False
        else:
            if not self.custom_open:
                return False
            closing = lower(name)
            if self.custom_open.get(closing, 0) <= 0:
                return False

        keys = self.keys
        prev_open = self.prev_open
        j = self.last_open
        while j >= 0:
            if key >= 0:
                if keys[j] != key:
                    j = prev_open[j]
                    continue
            elif keys[j] >= 0 or self.customs[j] != closing:
                j = prev_open[j]
                continue

            inner_end = tag_start - 2

            # Tags left open inside it: if the optional-end-tag rules apply to one of them,
            # scan its part again with the rules (fast mode only; rules mode has applied them).
            stack = self.stack
            if can_repair and not rules and not self.is_xml and self.stack_top > 0 and stack[self.stack_top - 1] > j:
                above = self.stack_top - 1
                while above > 0 and stack[above - 1] > j:
                    above -= 1
                target = self.first_misnested(above, self.stack_top)
                if target >= 0:
                    self.repair(target, tag_start - 1)
                    return True

            self.inner_ends[j] = inner_end
            self.ends[j] = gt

            while self.stack_top > 0 and stack[self.stack_top - 1] >= j:
                self.stack_top -= 1
                popped = keys[stack[self.stack_top]]
                if popped == P:
                    self.open_p -= 1
                if rules and popped >= 0:
                    self.open_by_key[popped] -= 1

            # It leaves the open list with everything opened after it; the tags opened inside it and
            # never closed end right before this end tag, as in a browser.
            self.truncate_open_list(j, inner_end)
            return False
        return False

    def start_tag(self, rules, tag_start, gt, to, first, name):
        """Adds the tag src[tag_start-1 .. gt]; returns the position to continue from (gt, or before a raw-text end
        tag). name: the name as the scan read it, None when it runs into '='."""
        src = self.src
        # The name in the inside of the opening tag (trimmed; "!--" for a comment). It starts at tag_start: the first
        # char (first) is a letter, '!' or '?'.
        last = src[gt - 1]
        if last >= "\x80" or first == "!" or name is None:
            end = gt
            if last >= "\x80":
                while end > tag_start and src[end - 1] in SPACE:
                    end -= 1
            if first == "!" and tag_start + 3 <= end and src[tag_start + 1] == "-" and src[tag_start + 2] == "-":
                length = 3
            elif name is None:
                # The name ends at whitespace or '/': <br/> is "br", as <br /> is.
                e = tag_start
                while e < end and src[e] not in ASCII_SPACE and src[e] != "/":
                    e += 1
                length = e - tag_start
            else:
                length = len(name)
                if tag_start + length > end:
                    length = end - tag_start
            name = src[tag_start:tag_start + length]
        else:
            length = len(name)
        key = -1
        custom_name = ""
        if length > 0:
            key = KEYS.get(name)
            if key is None:
                key = KEYS.get(ascii_lower(name), -1) if length <= 32 else -1
            if key < 0:
                # Custom names (svg elements, web components, ...): the lower-case name.
                custom_name = lower(name)
                self.custom_open.setdefault(custom_name, 0)
        flags = FLAGS[key] if key >= 0 and not self.is_xml else 0

        if flags:
            # Optional end tags (HTML5): <li> ends an open <li>, <td> an open <td>, ...
            if flags & F_RULE:
                rule = RULES[key]
                if rules:
                    # a target is open (in well-formed markup it is not)
                    if any(self.open_by_key[t] > 0 for t in rule.k):
                        self.close_in_scope(rule, tag_start - 2, True)
                elif self.stack_top > 0:
                    # The common case of a left-out end tag is caught right here: the innermost open tag is a target.
                    top = self.keys[self.stack[self.stack_top - 1]]
                    if top >= 0 and rule.t[top]:
                        self.apply_rule_in_fast_mode(rule, tag_start)

            # A block element ends an open <p>. Usually no <p> is open and this is one compare.
            if self.open_p > 0 and flags & F_CLOSES_P:
                if rules:
                    self.close_in_scope(P_RULE, tag_start - 2, True)
                else:
                    self.apply_rule_in_fast_mode(P_RULE, tag_start)

        # the innermost open tag is the parent
        parent = -1
        depth = 0
        if self.stack_top > 0:
            parent = self.stack[self.stack_top - 1]
            depth = self.depths[parent] + 1

        index = self.count
        # Void elements (<br>, <img>, <meta>, ...) and declarations never contain anything.
        is_void = flags & F_VOID != 0
        self_closing = is_void
        if not self_closing:
            if last == "/":
                self_closing = True
            elif last <= " ":
                self_closing = self._self_closing(tag_start, gt)

        if is_void:
            self._add(key, custom_name, parent, tag_start - 1, gt, depth, -1, False)
        else:
            self._add(key, custom_name, parent, tag_start - 1, gt, depth, self.last_open, self_closing)
            self.last_open = index
            if key >= 0:
                self.open_in_list[key] += 1
            elif custom_name:
                self.custom_open[custom_name] += 1
        self.count = index + 1

        if not self_closing:
            # Deeper than MAX_DEPTH the tag is not nested: it stays in the open list for its end tag.
            if self.stack_top < MAX_DEPTH:
                if self.stack_top < len(self.stack):
                    self.stack[self.stack_top] = index
                else:
                    self.stack.append(index)
                self.stack_top += 1
                if key == P:
                    self.open_p += 1
                if rules and key >= 0:
                    self.open_by_key[key] += 1

            # <script>, <style>, <textarea>, <title>, ...: the content is text, not markup;
            # continue right before the matching end tag.
            if flags & F_RAW_TEXT:
                return find_raw_text_end(src, gt + 1, RAW_TEXT_ENDS[key], to) - 1
        return gt

    # ----- implied end tags -----

    def first_misnested(self, start, to):
        """Among the tags left open at stack positions [start, to), the outermost one that a rule should have ended,
        or -1."""
        keys = self.keys
        stack = self.stack
        s = start
        while s < to - 1:
            key = keys[stack[s]]
            if key >= 0 and IS_RULE_TARGET[key]:
                inner = s + 1
                while inner < to:
                    inner_key = keys[stack[inner]]
                    if inner_key >= 0:
                        r = RULES[inner_key]
                        if r is not None and r.t[key]:
                            return s
                    inner += 1
            s += 1
        return -1

    def repair(self, stack_pos, region_end):
        """Scans again with the optional-end-tag rules from the tag at stack position stack_pos up to region_end:
        the tags from it on are replaced, the ones before it are kept."""
        first = self.stack[stack_pos]
        start = self.starts[first]

        # Past the budget the part is left as it is, so that parsing stays linear on any markup.
        cost = region_end - start
        if cost > self.budget:
            return
        self.budget -= cost

        self.truncate_open_list(first, -1)
        self.stack_top = stack_pos
        self.count = first
        for a in (self.keys, self.customs, self.parents, self.starts, self.ends, self.inner_starts, self.inner_ends,
                  self.depths, self.prev_open, self.self_closed):
            del a[first:]

        obk = self.open_by_key
        for k in range(len(obk)):
            obk[k] = 0
        self.open_p = 0
        for s in range(stack_pos):
            key = self.keys[self.stack[s]]
            if key >= 0:
                obk[key] += 1
                if key == P:
                    self.open_p += 1

        self.scan(True, start, region_end)

    def truncate_open_list(self, first, end):
        """Removes the tags from index first on from the open list (they are its tail).
        With end >= 0 the ones that have no end tag of their own end there."""
        k = self.last_open
        keys = self.keys
        while k >= first:
            key = keys[k]
            if key >= 0:
                self.open_in_list[key] -= 1
            else:
                custom = self.customs[k]
                if custom and custom in self.custom_open:
                    self.custom_open[custom] -= 1
            if end >= 0 and not self.self_closed[k] and self.inner_ends[k] == 0:
                self.inner_ends[k] = end
                self.ends[k] = end
            k = self.prev_open[k]
        self.last_open = k

    def scope_start(self, rule):
        """The stack position of the outermost target of the rule below the first stop tag, or -1."""
        start = -1
        s = self.stack_top - 1
        keys = self.keys
        stack = self.stack
        while s >= 0:
            k = keys[stack[s]]
            if k >= 0:
                if rule.t[k]:
                    start = s
                elif rule.s[k]:
                    break
            s -= 1
        return start

    def apply_rule_in_fast_mode(self, rule, tag_start):
        """Applies the rule for the tag that starts at tag_start. If a tag it ends was itself misnested, that part
        is scanned again with the rules first."""
        start = self.scope_start(rule)
        if start < 0:
            return
        m = self.first_misnested(start, self.stack_top)
        if m >= 0:
            self.repair(m, tag_start - 1)
            start = self.scope_start(rule)  # the part scanned again changed the stack
        self.close_from(start, tag_start - 2, False)

    def close_in_scope(self, rule, end, by_key):
        """Ends the outermost target found below the first stop tag together with everything opened inside it."""
        self.close_from(self.scope_start(rule), end, by_key)

    def close_from(self, start, end, by_key):
        """Ends the tag at stack position start (-1: none) together with everything opened inside it."""
        if start < 0:
            return
        first = self.stack[start]
        while self.stack_top > start:
            self.stack_top -= 1
            key = self.keys[self.stack[self.stack_top]]
            if key == P:
                self.open_p -= 1
            if by_key and key >= 0:
                self.open_by_key[key] -= 1
        self.truncate_open_list(first, end)
