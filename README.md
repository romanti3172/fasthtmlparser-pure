# fasthtmlparser-pure

[![CI](https://github.com/romanti3172/fasthtmlparser-pure/actions/workflows/ci.yml/badge.svg)](https://github.com/romanti3172/fasthtmlparser-pure/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/fasthtmlparser-pure.svg)](https://pypi.org/project/fasthtmlparser-pure/)

**Website:** [fasthtmlparser.com](https://fasthtmlparser.com): try the parser on any page, documentation and every language.

A fast HTML / XML parser for scraping, with **XPath 1.0** and **CSS selectors**, in **pure Python**: a port of
[z3l.FastHtmlParser](https://github.com/romanti3172/z3l.FastHtmlParser) with no compiled code and no dependencies. It
is built for extracting data, not for building a DOM: a page becomes a flat list of tags that hold only positions in
the source; text, attributes and entities are read when you ask for them.

It is **9x faster than BeautifulSoup with html.parser** (the other pure-Python option) and scrapes about as fast as
lxml, which is C. Where you can install compiled wheels, the Rust-backed
[`fasthtmlparser`](https://github.com/romanti3172/fasthtmlparser-py) is another 20x faster with **the same API**; this
package is for the places where you cannot: PyPy, Pyodide / the browser, restricted or embedded interpreters,
platforms without wheels, vendoring a single folder.

```
pip install fasthtmlparser-pure
```

Python 3.10 or later, CPython or PyPy; no dependencies.

```python
import fasthtmlparser_pure as fh

doc = fh.parse(html, url="https://books.toscrape.com/")  # str or bytes; url resolves relative links

# CSS selectors, as querySelectorAll / querySelector
for pod in doc.css("article.product_pod"):
    title = pod.css_first("h3 a").attr("title")
    price = pod.css_first("p.price_color").text
    link = pod.css_first("h3 a").href          # absolute URL
ul.css("> li")                                 # the children of ul

# XPath 1.0
doc.xpath("//div[@class='card']//span[@class='price']")   # tags
doc.xpath_values("//a[contains(@href, '/product/')]/@href")
doc.xpath_value("//dt[.='Price']/following-sibling::dd[1]")
doc.xpath_value("count(//table//tr)")          # "12"

# compile once, reuse on any number of pages and threads
TITLES = fh.XPathQuery("//article//h3/a/@title")
TITLES.values(doc)
PRICES = fh.CssQuery("p.price_color")
PRICES.all(doc)

# search by name (any name, case-insensitive) and criteria, as in BeautifulSoup
pod = doc.find("article", class_="product_pod")
pod.find("p", class_="price_color").text
doc.find_all("a", attr=("rel", "next"))
doc.find("dt", text="Price").find_next_sibling("dd").text
doc.find("my-widget", id_starts_with="w-")
# criteria: class_, id, attr=(name, value), text, id_starts_with, text_starts_with, body, body_starts_with, source

# reading a tag
tag.name, tag.depth, tag.index, tag.pos   # pos: byte offsets in the UTF-8 source
tag.text              # visible text, entities decoded, whitespace collapsed
tag.text_line_breaks  # <br> and block elements -> new lines, <p> and headings -> paragraphs
tag.attr("data-src")  # decoded value, "" when missing; attr_raw(): as written
tag.attrs             # {"href": "...", "class": "..."}
tag.href, tag.src     # absolute URLs; href_from(base) / src_from(base)
tag.mailto, tag.tel   # the address of a mailto: link, the number of a tel: link
tag.body, tag.outer_html, tag.source   # inner HTML, the whole element, the opening tag

# navigation
tag.parent, tag.children, tag.next_sibling, tag.previous_sibling, tag.next, tag.previous
tag.find_parent("table"), tag.find_next("h2"), tag.text_after_until("br")

# the whole document
len(doc), doc[0], doc[-1], list(doc)    # every tag, comments included
```

To use the Rust package where it is installed and this one elsewhere:

```python
try:
    import fasthtmlparser as fh
except ImportError:
    import fasthtmlparser_pure as fh
```

A missing tag is `None`, a missing value is `""`. An invalid selector raises `fh.CssSelectorError`, an invalid XPath
expression `fh.XPathError` (both are `ValueError`s); `xpath()` on an expression that gives a number or string raises
`XPathError` too (use `xpath_value`). The string methods compile and cache queries. A `Node` keeps its document alive,
nodes compare equal when they are the same tag, and are hashable. Documents and compiled queries are read-only and can
be shared between threads. Type stubs are included.

CSS: type, `*`, `#id`, `.class`, attribute selectors with all operators and the `i` / `s` flags, the four combinators,
selector lists, `:first-child`, `:last-child`, `:only-child`, `:*-of-type`, `:nth-child(An+B [of S])` and its relatives,
`:not()`, `:is()`, `:where()`, `:has()`, `:empty`, `:root`, `:scope`, `:link`, `:checked`, `:disabled`, `:enabled`,
`:required`, `:optional`, `:contains("text")`. XPath: all axes except namespace, predicates, `|`, the operators and all
XPath 1.0 functions, plus `ends-with()`, `lower-case()`, `upper-case()`. HTML rules apply: names are case-insensitive,
the string-value of an element is its visible text, attribute values and text are decoded. HTML5 optional end tags
(`<li>`, `<p>`, `<td>`, `<option>` without end tags) give the same elements as in a browser; there is no full HTML5
tree construction (no implied `<tbody>` or `<html>`), and documents are read-only.

## Same results as the .NET version

`tests/test_conformance.py` runs the conformance data of the other ports: on 121 pages (fixtures, broken markup,
cut-off pages, CR LF, 600-deep nesting, ...) every tag's name, position, text, text with line breaks, attributes,
navigation and links, and 569 XPath / CSS queries on the document and from three tags of each page, must give exactly
what z3l.FastHtmlParser (.NET) gives. `tests/test_api.py` is the test suite of the Rust-backed package, unchanged.

## Benchmarks

`python bench/bench.py` (Python 3.14, AMD Ryzen 7 3800X, Windows; [`bench-results.md`](https://github.com/romanti3172/fasthtmlparser-pure/blob/main/bench-results.md)). Every
library must extract the same records before anything is timed. The first two columns are pure Python; the others are
C or Rust, for reference.

Parse a page:

| Page | fasthtmlparser-pure | BeautifulSoup (html.parser) | lxml | selectolax (lexbor) | fasthtmlparser (Rust) |
|---|---:|---:|---:|---:|---:|
| quotes, 11 KB | 577 μs | 5,488 μs | 486 μs | 96 μs | 21 μs |
| laptops, 38 KB | 1,724 μs | 15,287 μs | 1,554 μs | 349 μs | 67 μs |
| books, 51 KB | 2,081 μs | 18,907 μs | 1,563 μs | 309 μs | 70 μs |
| books x10, 510 KB | 19,792 μs | 188,407 μs | 15,169 μs | 3,417 μs | 727 μs |
| books without `</li>` `</p>` | 2,839 μs | 17,465 μs | 1,511 μs | 231 μs | 70 μs |

Scrape a page: parse and extract the records (books: 20 × title, price, rating; quotes: 10 × text, author, tags).

| Task | fasthtmlparser-pure | BeautifulSoup (html.parser) | lxml / parsel | selectolax | fasthtmlparser (Rust) |
|---|---:|---:|---:|---:|---:|
| 20 books, `find` | 2,610 μs | | | | 138 μs |
| 20 books, CSS | 2,827 μs | 22,535 μs | parsel 2,552 μs | 579 μs | |
| 20 books, XPath | 3,906 μs | | lxml 2,778 μs | | |
| 10 quotes | 949 μs | 6,770 μs | parsel 1,057 μs | 282 μs | 66 μs |

## Development

```
python -m venv .venv && .venv/Scripts/activate   # or . .venv/bin/activate
pip install -e . pytest
python -m pytest
```

`tools/gen.py` rebuilds the tag list and the HTML5 entity table (`fasthtmlparser_pure/_tables.py`) from the C# sources.

## License

Same terms as z3l.FastHtmlParser (see LICENSE.md): free for companies under USD 1M annual revenue and under 100
people, a commercial license above that. This is source-available, not an OSI open-source license.
