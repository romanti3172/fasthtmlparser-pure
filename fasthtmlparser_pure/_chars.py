"""Character helpers shared by the parser, the text readers and the query engines.

Inside the library the page is a Python str and positions are char indexes; Node.pos converts them to byte offsets
into the UTF-8 source. Whitespace is what .NET's char.IsWhiteSpace sees (str.isspace() also takes U+001C..U+001F).
"""

import re

# ASCII whitespace as the parser sees it: space, \t, \n, \v, \f, \r.
ASCII_SPACE = " \t\n\x0b\x0c\r"
# One whitespace char as .NET char.IsWhiteSpace sees it.
SPACE = ASCII_SPACE + "".join(map(chr, (0x85, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F,
                                         0x3000)))
SPACE_SET = frozenset(SPACE)
SPACE_RUNS = re.compile("[" + re.escape(SPACE) + "]+")
HAS_SPACE = re.compile("[" + re.escape(SPACE) + "]")
ASTRAL = re.compile("[\U00010000-\U0010ffff]")

_ASCII_LOWER = {c: c + 32 for c in range(65, 91)}
_ASCII_UPPER = {c: c - 32 for c in range(97, 123)}


def ascii_lower(s):
    """Lower case of ASCII letters only (str.lower() also maps U+212A KELVIN SIGN to 'k'); keeps the length."""
    return s.lower() if s.isascii() else s.translate(_ASCII_LOWER)


def lower(s):
    """Lower case: ASCII, and Unicode."""
    return s.lower()


def upper(s):
    return s.upper()


def trim_space(s):
    """Trims Unicode whitespace (char.IsWhiteSpace) from both ends."""
    return s.strip(SPACE)


def trim_range(s, start, end):
    """s[start:end] clamped to the string and trimmed of Unicode whitespace."""
    if start < 0:
        start = 0
    if start >= end:
        return ""
    return s[start:end].strip(SPACE)


def utf16_length(s):
    """The length in UTF-16 code units, as .NET counts."""
    n = len(s)
    if s.isascii():
        return n
    return n + len(ASTRAL.findall(s))


def equal_fold(a, b):
    """Case-insensitive comparison (ASCII fast path, Unicode lower case otherwise)."""
    if a == b:
        return True
    if a.isascii() and b.isascii():
        return len(a) == len(b) and a.lower() == b.lower()
    if utf16_length(a) != utf16_length(b):
        return False
    return a.lower() == b.lower()


def ascii_equal_fold(a, b):
    """ASCII-only case-insensitive comparison."""
    return len(a) == len(b) and ascii_lower(a) == ascii_lower(b)


def is_alpha(c):
    return ("a" <= c <= "z") or ("A" <= c <= "Z")


def is_digit(c):
    return "0" <= c <= "9"


def is_hex(c):
    return ("0" <= c <= "9") or ("a" <= c <= "f") or ("A" <= c <= "F")


def quote(s):
    """A string in double quotes, for error messages."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


class ByteOffsets:
    """Char index to UTF-8 byte offset for one string: the non-ASCII chars and the extra bytes before each."""

    __slots__ = ("_at", "_extra")

    def __init__(self, s):
        at = []
        extra = []
        total = 0
        for m in re.finditer(r"[^\x00-\x7f]+", s):
            run = m.group()
            for k, c in enumerate(run):
                o = ord(c)
                total += 1 if o < 0x800 else (2 if o < 0x10000 else 3)
                at.append(m.start() + k)
                extra.append(total)
        self._at = at
        self._extra = extra

    def __call__(self, i):
        """The byte offset of the char at index i."""
        at = self._at
        if not at or i <= at[0]:
            return i
        # the number of non-ASCII chars before i
        lo, hi = 0, len(at)
        while lo < hi:
            mid = (lo + hi) >> 1
            if at[mid] < i:
                lo = mid + 1
            else:
                hi = mid
        return i + self._extra[lo - 1]
