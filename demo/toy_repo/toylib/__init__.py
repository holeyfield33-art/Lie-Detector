"""toylib: a tiny string/number utility library used by the Lie Detector demo.

One function (`count_words`) is deliberately buggy so its README claim is
provably false.
"""

import unicodedata

__version__ = "1.0.0"


def add(a, b):
    """Return the sum of two numbers."""
    return a + b


def slugify(text):
    """Lowercase, normalise and hyphenate a string into a URL slug."""
    normalised = unicodedata.normalize("NFKC", text)
    return "-".join(normalised.lower().split())


def count_words(text):
    """Count words in a string.

    Deliberately buggy: splitting on a single space miscounts runs of
    whitespace ("a  b" -> 3, not 2).
    """
    if not text:
        return 0
    return len(text.split(" "))
