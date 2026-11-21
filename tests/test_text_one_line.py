"""text is the visible text in one line, as jsoup's text() (the cases of TextOneLineTests in the .NET version)."""

import pytest

import fasthtmlparser_pure as fh

CASES = [
    ("line1<br>line2", "line1 line2"),
    ("line1<br><br><br>line2", "line1 line2"),
    ("line1 <br> line2", "line1 line2"),
    ("line1<BR/>line2", "line1 line2"),
    ("<p>a</p><p>b</p>", "a b"),
    ("<ul><li>a</li><li>b</li></ul>", "a b"),
    ("<table><tr><td>Price</td><td>100</td></tr></table>", "Price 100"),
    ("<h1>Title</h1>text", "Title text"),
    ("a<span>b</span>c", "abc"),
    ("a<my-widget>b</my-widget>", "ab"),
    ("a\nb", "a b"),
    ("a\r\n\t  b", "a b"),
    ("a<!-- c -->b", "ab"),
    ("<p>a</p><script>x()</script><p>b</p>", "a b"),
    ("a &amp; b", "a & b"),
    ("x<pre>a\n  b</pre>y", "x a\n  b y"),
]


@pytest.mark.parametrize("html,text", CASES)
def test_text_is_one_line(html, text):
    doc = fh.parse("<div>" + html + "</div>")
    assert doc.find("div").text == text
    assert len(doc.find_all("div", text=text)) == 1


def test_leaf_pre_and_raw_text():
    doc = fh.parse("<span>a\n   b</span><pre>c\n  d</pre><textarea>e\n  f</textarea>")
    assert doc.find("span").text == "a b"
    assert doc.find("pre").text == "c\n  d"
    assert doc.find("textarea").text == "e\n  f"
