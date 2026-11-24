"""Fast HTML / XML parser for scraping with XPath 1.0 and CSS selectors, in pure Python.

A pure-Python port of z3l.FastHtmlParser: a page becomes a flat list of tags that hold only positions in the source;
text, attributes and entities are read when they are asked for. The API is the one of the `fasthtmlparser` package
(Rust inside), so code runs on either.

    >>> import fasthtmlparser_pure as fh
    >>> doc = fh.parse('<ul><li class="a">one<li>two</ul>')
    >>> [li.text for li in doc.css("li")]
    ['one', 'two']
    >>> doc.xpath_value("count(//li)")
    '2'
"""

from ._api import (
    CssQuery,
    CssSelectorError,
    Document,
    Node,
    XPathError,
    XPathQuery,
    decode_html,
    html_to_text,
    html_to_text_line_breaks,
    parse,
)

__version__ = "0.1.4"

__all__ = [
    "CssQuery",
    "CssSelectorError",
    "Document",
    "Node",
    "XPathError",
    "XPathQuery",
    "decode_html",
    "html_to_text",
    "html_to_text_line_breaks",
    "parse",
]
