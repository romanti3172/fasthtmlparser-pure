"""HTML5 character reference decoder (WHATWG "character reference state"): all 2125 named references, the legacy
names without ';', numeric references with or without ';', and the spec replacements (0, surrogates, > U+10FFFF ->
U+FFFD; 0x80-0x9F -> Windows-1252)."""

from ._tables import ENTITIES, LEGACY, MAX_LEGACY_NAME, MAX_NAME

# Replacements for numeric references 0x80..0x9F; 0 = keep the code point as is.
WINDOWS_1252 = (
    0x20AC, 0, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0, 0x017D, 0,
    0, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0, 0x017E,
    0x0178,
)

_ALNUM = frozenset("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_HEX = {c: int(c, 16) for c in "0123456789abcdefABCDEF"}


def needs_decoding(s):
    """Whether a value read from the page differs from its source: it has a character reference or a CR."""
    return "&" in s or "\r" in s


def normalize_newlines(s):
    """HTML input stream preprocessing: CR LF and a lone CR become LF."""
    if "\r" not in s:
        return s
    return s.replace("\r\n", "\n").replace("\r", "\n")


def decode(s, in_attribute):
    """Decodes the character references in s; CR LF and a lone CR become LF. With in_attribute, a legacy name
    without ';' followed by '=' or an alphanumeric is left as is (so "?a=1&copy=2" stays intact)."""
    # Line breaks first, as a browser reads the page; a CR written as "&#13;" is decoded after that and stays.
    if "\r" in s:
        s = s.replace("\r\n", "\n").replace("\r", "\n")
    amp = s.find("&")
    if amp < 0:
        return s
    n = len(s)
    out = []
    pos = 0
    while amp >= 0:
        if amp > pos:
            out.append(s[pos:amp])
        text = None
        consumed = 0
        if amp + 1 < n:
            text, consumed = reference(s, amp + 1, in_attribute)
        if text is None:
            out.append("&")  # not a reference: keep '&'
            pos = amp + 1
        else:
            out.append(text)
            pos = amp + 1 + consumed
        amp = s.find("&", pos) if pos < n else -1
    if pos < n:
        out.append(s[pos:])
    return "".join(out)


def decode_reference(s, at):
    """Decodes one character reference; s[at] is the char right after '&'. Returns (text, chars consumed after '&'),
    or (None, 0) when there is no reference."""
    if at >= len(s):
        return None, 0
    return reference(s, at, False)


def reference(s, at, in_attribute):
    if s[at] == "#":
        cp, consumed = match_numeric(s, at)
        if cp < 0:
            return None, 0
        return chr(cp), consumed
    return match_named(s, at, in_attribute)


def match_numeric(s, at):
    """s[at] is '#'. Returns (code point (-1: not a reference), chars consumed)."""
    n = len(s)
    i = at + 1
    c = s[i] if i < n else ""
    is_hex = c == "x" or c == "X"
    if is_hex:
        i += 1
    digits = i
    value = 0
    if is_hex:
        while i < n:
            d = _HEX.get(s[i])
            if d is None:
                break
            value = value * 16 + d
            if value > 0x10FFFF:
                value = 0x110000  # saturate on long digit runs
            i += 1
    else:
        while i < n:
            c = s[i]
            if c < "0" or c > "9":
                break
            value = value * 10 + ord(c) - 48
            if value > 0x10FFFF:
                value = 0x110000
            i += 1
    if i == digits:
        return -1, 0  # "&#" / "&#x" without digits is plain text
    if i < n and s[i] == ";":
        i += 1
    if value == 0 or value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
        value = 0xFFFD
    elif 0x80 <= value <= 0x9F:
        mapped = WINDOWS_1252[value - 0x80]
        if mapped:
            value = mapped
    return value, i - at


def match_named(s, at, in_attribute):
    """s[at] is the char right after '&'. Returns (decoded text (None: not a reference), chars consumed)."""
    length = len(s)
    n = 0
    limit = MAX_NAME + 1
    while n < limit and at + n < length and s[at + n] in _ALNUM:
        n += 1
    if n < 2:
        return None, 0
    # Full match: "&name;".
    if at + n < length and s[at + n] == ";":
        v = ENTITIES.get(s[at:at + n])
        if v is not None:
            return v, n + 1
    # Longest legacy prefix without ';': "&copy2024" -> "(c)2024", "&notit;" -> "(not)it;".
    k = n if n < MAX_LEGACY_NAME else MAX_LEGACY_NAME
    while k >= 2:
        name = s[at:at + k]
        if name in LEGACY:
            if in_attribute and at + k < length:
                c = s[at + k]
                if c == "=" or c in _ALNUM:
                    break
            return ENTITIES[name], k
        k -= 1
    return None, 0
