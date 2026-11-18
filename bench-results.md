Results of `python bench/bench.py` (2026-11-18, AMD Ryzen 7 3800X, Windows 10, fasthtmlparser 0.1.0, selectolax 1.0.2, lxml 6.1.3, parsel 1.12.1, beautifulsoup4 4.15.0).

Python 3.14.10, Windows, AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD

| page | fasthtmlparser_pure | BeautifulSoup (html.parser) | fasthtmlparser (Rust) | selectolax (lexbor) | lxml | parsel (lxml) | BeautifulSoup (lxml) |
|---|---:|---:|---:|---:|---:|---:|---:|
| quotes, 11 KB | 577 μs | 5,488 μs | 21 μs | 96 μs | 486 μs | 508 μs | 4,317 μs |
| laptops, 38 KB | 1,724 μs | 15,287 μs | 67 μs | 349 μs | 1,554 μs | 1,618 μs | 11,934 μs |
| books, 51 KB | 2,081 μs | 18,907 μs | 70 μs | 309 μs | 1,563 μs | 1,631 μs | 14,747 μs |
| books x10, 510 KB | 19,792 μs | 188,407 μs | 727 μs | 3,417 μs | 15,169 μs | 15,567 μs | 15,133 μs |
| books without </li> </p> | 2,839 μs | 17,465 μs | 70 μs | 231 μs | 1,511 μs | 1,627 μs | 13,869 μs |

| scrape 20 books | time |
|---|---:|
| fasthtmlparser_pure find | 2,610 μs |
| fasthtmlparser_pure CSS | 2,827 μs |
| fasthtmlparser_pure XPath | 3,906 μs |
| BeautifulSoup (html.parser) CSS | 22,535 μs |
| fasthtmlparser (Rust) find | 138 μs |
| selectolax CSS | 579 μs |
| lxml XPath | 2,778 μs |
| parsel CSS | 2,552 μs |
| BeautifulSoup (lxml) CSS | 19,188 μs |

| scrape 10 quotes | time |
|---|---:|
| fasthtmlparser_pure find | 949 μs |
| BeautifulSoup (html.parser) CSS | 6,770 μs |
| fasthtmlparser (Rust) find | 66 μs |
| selectolax CSS | 282 μs |
| parsel CSS | 1,057 μs |
| BeautifulSoup (lxml) CSS | 5,852 μs |
