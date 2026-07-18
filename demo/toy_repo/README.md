# toylib

A tiny string and number utility library.

## Facts

`toylib.add(2, 3)` returns `5`.

`toylib.slugify("Hello World")` returns `"hello-world"`.

`toylib.slugify` accepts Unicode input, including multi-byte emoji, without raising exceptions.

`toylib.count_words("a  b")` returns `2`.

toylib is blazing fast, processing millions of strings per second.

Rust bindings are planned for a future release.

## Install

```bash
pip install toylib
```
