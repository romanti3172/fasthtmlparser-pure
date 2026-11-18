"""Links: href / src made absolute against the page URL, written as .NET's System.Uri writes them.

Resolution follows RFC 3986 the way the Go port does it (net/url: Parse + ResolveReference), byte for byte: the URL
parts here are UTF-8 bytes.
"""

import re

from . import _decode as dec
from ._chars import ascii_lower, trim_space


class UrlError(Exception):
    pass


class Url:
    __slots__ = ("scheme", "opaque", "user", "host", "path", "raw_path", "force_query", "raw_query", "fragment",
                 "raw_fragment")

    def __init__(self):
        self.scheme = b""
        self.opaque = b""
        self.user = None  # the userinfo as written back (escaped), or None
        self.host = b""
        self.path = b""  # decoded
        self.raw_path = b""  # the path as written, when it differs from the default escaping of path
        self.force_query = False
        self.raw_query = b""
        self.fragment = b""  # decoded
        self.raw_fragment = b""

    def copy(self):
        u = Url()
        for k in Url.__slots__:
            setattr(u, k, getattr(self, k))
        return u


MODE_PATH = 0
MODE_HOST = 1
MODE_USER_PASSWORD = 2
MODE_FRAGMENT = 3

DEFAULT_PORTS = {b"http": b"80", b"https": b"443", b"ftp": b"21", b"ws": b"80", b"wss": b"443"}
RESERVED_FOR_DISPLAY = frozenset(b"%/?#[]@:!$&'()*+,;=")
UPPER_HEX = b"0123456789ABCDEF"
HOST_ALLOWED = frozenset(b"!$&'()*+,;=:[]<>\"")
VALID_ENCODED = frozenset(b"!$&'()*+,;=:@[]%")
USERINFO = re.compile(rb"\A[a-zA-Z0-9\-._:~!$&'()*+,;=%@]*\Z")
CONTROL = re.compile(rb"[\x00-\x1f\x7f]")
HEX = frozenset(b"0123456789abcdefABCDEF")


def _should_escape(c, mode):
    if 0x61 <= c <= 0x7A or 0x41 <= c <= 0x5A or 0x30 <= c <= 0x39:
        return False
    if mode == MODE_HOST and c in HOST_ALLOWED:
        return False
    if c in b"-_.~":
        return False
    if c in b"$&+,/:;=?@" and mode != MODE_HOST:
        if mode == MODE_PATH:
            return c == 0x3F  # '?'
        if mode == MODE_USER_PASSWORD:
            return c in b"@/?:"
        return False
    if mode == MODE_FRAGMENT and c in b"!()*":
        return False
    return True


# escape decisions by byte, per mode
SHOULD_ESCAPE = tuple(tuple(_should_escape(c, m) for c in range(256)) for m in range(4))


def _unhex(c):
    return c - 48 if c <= 57 else (c | 0x20) - 87


def unescape(s, mode):
    """net/url unescape; raises UrlError on a malformed URL."""
    count = 0
    n = len(s)
    table = SHOULD_ESCAPE[mode]
    i = 0
    while i < n:
        c = s[i]
        if c == 37:
            count += 1
            if i + 2 >= n or s[i + 1] not in HEX or s[i + 2] not in HEX:
                raise UrlError("invalid URL escape")
            # in the host, %-encoding is only for non-ASCII bytes (and %25)
            if mode == MODE_HOST and _unhex(s[i + 1]) < 8 and s[i:i + 3] != b"%25":
                raise UrlError("invalid URL escape")
            i += 3
        else:
            if mode == MODE_HOST and c < 0x80 and table[c]:
                raise UrlError("invalid character in host name")
            i += 1
    if count == 0:
        return s
    out = bytearray()
    i = 0
    while i < n:
        c = s[i]
        if c == 37:
            out.append((_unhex(s[i + 1]) << 4) | _unhex(s[i + 2]))
            i += 3
        else:
            out.append(c)
            i += 1
    return bytes(out)


def escape(s, mode):
    table = SHOULD_ESCAPE[mode]
    out = None
    start = 0
    for i, c in enumerate(s):
        if table[c]:
            if out is None:
                out = bytearray()
            out += s[start:i]
            out.append(37)
            out.append(UPPER_HEX[c >> 4])
            out.append(UPPER_HEX[c & 15])
            start = i + 1
    if out is None:
        return s
    out += s[start:]
    return bytes(out)


def _valid_encoded(s, mode):
    table = SHOULD_ESCAPE[mode]
    for c in s:
        if c in VALID_ENCODED:
            continue
        if table[c]:
            return False
    return True


# ----- parsing -----

def _get_scheme(raw):
    for i, c in enumerate(raw):
        if 0x61 <= (c | 0x20) <= 0x7A:
            continue
        if 0x30 <= c <= 0x39 or c == 43 or c == 45 or c == 46:
            if i == 0:
                return b"", raw
            continue
        if c == 58:
            if i == 0:
                raise UrlError("missing protocol scheme")
            return raw[:i], raw[i + 1:]
        return b"", raw
    return b"", raw


def _set_path(u, p):
    u.path = unescape(p, MODE_PATH)
    u.raw_path = b"" if escape(u.path, MODE_PATH) == p else p


def _escaped_path(u):
    if u.raw_path and _valid_encoded(u.raw_path, MODE_PATH):
        try:
            if unescape(u.raw_path, MODE_PATH) == u.path:
                return u.raw_path
        except UrlError:
            pass
    if u.path == b"*":
        return b"*"
    return escape(u.path, MODE_PATH)


def _escaped_fragment(u):
    if u.raw_fragment and _valid_encoded(u.raw_fragment, MODE_FRAGMENT):
        try:
            if unescape(u.raw_fragment, MODE_FRAGMENT) == u.fragment:
                return u.raw_fragment
        except UrlError:
            pass
    return escape(u.fragment, MODE_FRAGMENT)


def _valid_optional_port(port):
    if not port:
        return True
    if port[0] != 58:
        return False
    return all(48 <= c <= 57 for c in port[1:])


def _parse_host(host):
    if host.startswith(b"["):
        i = host.rfind(b"]")
        if i < 0:
            raise UrlError("missing ']' in host")
        if not _valid_optional_port(host[i + 1:]):
            raise UrlError("invalid port")
    else:
        i = host.rfind(b":")
        if i >= 0 and not _valid_optional_port(host[i:]):
            raise UrlError("invalid port")
    return unescape(host, MODE_HOST)


def _parse_authority(u, authority):
    i = authority.rfind(b"@")
    u.host = _parse_host(authority if i < 0 else authority[i + 1:])
    if i < 0:
        return
    userinfo = authority[:i]
    if not USERINFO.match(userinfo):
        raise UrlError("invalid userinfo")
    colon = userinfo.find(b":")
    if colon < 0:
        u.user = escape(unescape(userinfo, MODE_USER_PASSWORD), MODE_USER_PASSWORD)
    else:
        name = unescape(userinfo[:colon], MODE_USER_PASSWORD)
        password = unescape(userinfo[colon + 1:], MODE_USER_PASSWORD)
        u.user = escape(name, MODE_USER_PASSWORD) + b":" + escape(password, MODE_USER_PASSWORD)


def parse_url(raw_url):
    """net/url Parse."""
    h = raw_url.find(b"#")
    u = _parse_no_fragment(raw_url if h < 0 else raw_url[:h])
    if h >= 0:
        frag = raw_url[h + 1:]
        u.fragment = unescape(frag, MODE_FRAGMENT)
        u.raw_fragment = b"" if escape(u.fragment, MODE_FRAGMENT) == frag else frag
    return u


def _parse_no_fragment(raw):
    if CONTROL.search(raw):
        raise UrlError("invalid control character in URL")
    u = Url()
    if raw == b"*":
        u.path = b"*"
        return u
    scheme, rest = _get_scheme(raw)
    u.scheme = scheme.lower()

    q = rest.find(b"?")
    if q >= 0 and q == len(rest) - 1:
        u.force_query = True
        rest = rest[:q]
    elif q >= 0:
        u.raw_query = rest[q + 1:]
        rest = rest[:q]

    if not rest.startswith(b"/"):
        if u.scheme:
            # rootless paths are opaque (RFC 3986)
            u.opaque = rest
            return u
        # a relative-path reference: the first segment cannot contain ':'
        slash = rest.find(b"/")
        first = rest if slash < 0 else rest[:slash]
        if b":" in first:
            raise UrlError("first path segment in URL cannot contain colon")

    if (u.scheme or not rest.startswith(b"///")) and rest.startswith(b"//"):
        authority = rest[2:]
        rest = b""
        slash = authority.find(b"/")
        if slash >= 0:
            rest = authority[slash:]
            authority = authority[:slash]
        _parse_authority(u, authority)
    _set_path(u, rest)
    return u


def _resolve_path(base, ref):
    if not ref:
        full = base
    elif ref[0] != 47:
        slash = base.rfind(b"/")
        full = (b"" if slash < 0 else base[:slash + 1]) + ref
    else:
        full = ref
    if not full:
        return b""
    dst = bytearray(b"/")
    first = True
    remaining = full
    found = True
    elem = b""
    while found:
        slash = remaining.find(b"/")
        found = slash >= 0
        elem = remaining[:slash] if found else remaining
        remaining = remaining[slash + 1:] if found else b""
        if elem == b".":
            first = False
            continue
        if elem == b"..":
            s = bytes(dst[1:])
            index = s.rfind(b"/")
            dst = bytearray(b"/")
            if index < 0:
                first = True
            else:
                dst += s[:index]
        else:
            if not first:
                dst.append(47)
            dst += elem
            first = False
    if elem == b"." or elem == b"..":
        dst.append(47)
    if len(dst) > 1 and dst[1] == 47:
        dst = dst[1:]
    return bytes(dst)


def resolve_reference(base, ref):
    """net/url ResolveReference."""
    url = ref.copy()
    if not ref.scheme:
        url.scheme = base.scheme
    if ref.scheme or ref.host or ref.user is not None:
        _set_path(url, _resolve_path(_escaped_path(ref), b""))
        return url
    if ref.opaque:
        url.user = None
        url.host = b""
        url.path = b""
        return url
    if not ref.path and not ref.force_query and not ref.raw_query:
        url.raw_query = base.raw_query
        if not ref.fragment:
            url.fragment = base.fragment
            url.raw_fragment = base.raw_fragment
    if not ref.path and base.opaque:
        url.opaque = base.opaque
        url.user = None
        url.host = b""
        url.path = b""
        return url
    url.host = base.host
    url.user = base.user
    _set_path(url, _resolve_path(_escaped_path(base), _escaped_path(ref)))
    return url


def _split_host_port(host_port):
    host = host_port
    port = b""
    colon = host.rfind(b":")
    if colon >= 0 and _valid_optional_port(host[colon:]):
        port = host[colon + 1:]
        host = host[:colon]
    if len(host) >= 2 and host[0] == 91 and host[-1] == 93:
        host = host[1:-1]
    return host, port


def _valid_utf8(b):
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _unescape_for_display(s):
    """Decodes %XX escapes except those of reserved and control characters; escaped bytes of non-ASCII text are
    decoded when they form valid UTF-8."""
    if b"%" not in s:
        return s
    out = bytearray()
    n = len(s)
    i = 0
    while i < n:
        if s[i] != 37 or i + 2 >= n or s[i + 1] not in HEX or s[i + 2] not in HEX:
            out.append(s[i])
            i += 1
            continue
        c = (_unhex(s[i + 1]) << 4) | _unhex(s[i + 2])
        if c < 0x80:
            if c < 0x20 or c == 0x7F or c in RESERVED_FOR_DISPLAY:
                out += s[i:i + 3]
            else:
                out.append(c)
            i += 3
            continue
        # a run of escaped bytes >= 0x80: decoded together when it is valid UTF-8
        j = i
        run = bytearray()
        while j + 2 < n and s[j] == 37 and s[j + 1] in HEX and s[j + 2] in HEX:
            v = (_unhex(s[j + 1]) << 4) | _unhex(s[j + 2])
            if v < 0x80:
                break
            run.append(v)
            j += 3
        out += run if _valid_utf8(bytes(run)) else s[i:j]
        i = j
    return bytes(out)


def display_string(u):
    """Writes a resolved URL as the .NET version does (System.Uri.ToString): the scheme and host in lower case, no
    default port, an empty path as "/", and percent-escapes decoded except those of the characters that delimit URL
    parts ("%2F" stays) and of control characters; non-ASCII text is written as is."""
    scheme = u.scheme.lower()
    b = bytearray(scheme + b":")
    if not u.opaque:
        if u.host or u.user is not None:
            b += b"//"
            if u.user is not None:
                b += u.user + b"@"
            host, port = _split_host_port(u.host)
            b += host.decode("utf-8", "replace").lower().encode("utf-8")
            if port and port != DEFAULT_PORTS.get(scheme):
                b += b":" + port
        path = _escaped_path(u)
        if not path and u.host:
            path = b"/"
        b += _unescape_for_display(path)
    else:
        b += u.opaque
    if u.raw_query or u.force_query:
        b += b"?" + _unescape_for_display(u.raw_query)
    if u.fragment:
        b += b"#" + _unescape_for_display(_escaped_fragment(u))
    return bytes(b).decode("utf-8", "replace")


def _b(s):
    return s.encode("utf-8", "surrogatepass")


def absolute_url(s):
    """Parses a page URL with its own scheme and host; None for anything else."""
    if not s:
        return None
    try:
        u = parse_url(_b(s))
    except UrlError:
        return None
    if not u.scheme or (not u.host and not u.opaque and u.scheme != b"file"):
        return None
    return u


def _absolute_link(link):
    """A link with its own scheme ("https://...", "mailto:...", "file:..."). "/path" and "//host/path" are
    relative."""
    try:
        return bool(parse_url(_b(trim_space(link))).scheme)
    except UrlError:
        return False


def correct_link(raw, page_url, base):
    """The raw attribute value with its character references decoded and made absolute against the page.
    Percent-escapes are part of the URL and are kept. An empty link gives the page URL."""
    if not raw:
        return page_url
    link = dec.decode(raw, True) if "&" in raw or "\r" in raw else raw
    if base is None or _absolute_link(link):
        return link
    try:
        return display_string(resolve_reference(base, parse_url(_b(trim_space(link)))))
    except UrlError:
        return link


def after_scheme(raw_href, scheme):
    """Everything after the first ':' of a link with the scheme (mailto, tel), or ""."""
    href = trim_space(dec.decode(raw_href, True))
    if len(href) < len(scheme) or ascii_lower(href[:len(scheme)]) != scheme:
        return ""
    i = href.find(":")
    return "" if i < 0 else href[i + 1:]
