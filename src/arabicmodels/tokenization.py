"""Whitespace tokenization with character offsets.

Every word-level model in this package labels *whitespace tokens* and keeps
each token's offset into the original string, so predictions can always be
mapped back onto the untouched source text.
"""
from dataclasses import dataclass


@dataclass
class Token:
    idx: int      # position in the sequence
    text: str     # surface form, exactly as in the source
    start: int    # character offset of the first character
    end: int      # character offset one past the last character


def whitespace_tokenize(text: str) -> list[Token]:
    """Split on whitespace, preserving each token's character span."""
    tokens: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        j = i
        while j < n and not text[j].isspace():
            j += 1
        tokens.append(Token(idx=len(tokens), text=text[i:j], start=i, end=j))
        i = j
    return tokens
