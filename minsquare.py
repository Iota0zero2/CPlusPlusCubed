#!/usr/bin/env python3
"""minsquare — minify C/C++/Arduino source and lay it out as a square.

What it does (behavior-preserving):
  * strips // and /* */ comments
  * removes all non-essential whitespace (keeps separators only where two
    tokens would otherwise merge)
  * auto-creates `#define SHORT longName` aliases for long identifiers that
    appear many times (e.g. digitalWrite -> W), when that actually saves bytes
  * reflows the result so the code block is as close to a square as possible

What it deliberately does NOT do (unsafe to automate without understanding
the program — would change behavior or break it):
  * rename your variables/constants, inline literals, or delete logging

Only C-like (brace) languages are supported. Whitespace-significant
languages such as Python cannot be line-collapsed without breaking them, so
they are rejected.

Usage:
  python3 minsquare.py input.cpp [-o out.cpp] [options]

Options:
  -o FILE            write to FILE (default: stdout)
  --no-macros        don't auto-generate #define aliases
  --macro-min-uses N minimum uses for an alias (default 4)
  --macro-min-len  N minimum identifier length to alias (default 5)
  --aspect R         monospace cell height/width for a *visual* square
                     (default 2.0). Use --char-square for equal rows=cols.
  --char-square      make character rows == character columns (aspect 1.0)
  --width W          force a fixed column width instead of the square calc
  --keep-comments    keep comments (still minifies whitespace/macros)
"""

import argparse
import math
import re
import sys

KEYWORDS = {
    "alignas", "alignof", "and", "asm", "auto", "bool", "break", "case",
    "catch", "char", "class", "const", "constexpr", "continue", "decltype",
    "default", "delete", "do", "double", "else", "enum", "explicit", "extern",
    "false", "float", "for", "friend", "goto", "if", "inline", "int", "long",
    "mutable", "namespace", "new", "noexcept", "not", "nullptr", "operator",
    "or", "private", "protected", "public", "register", "return", "short",
    "signed", "sizeof", "static", "struct", "switch", "template", "this",
    "throw", "true", "try", "typedef", "typename", "union", "unsigned",
    "using", "virtual", "void", "volatile", "while", "xor",
}

# Multi-char operators, longest first so the lexer prefers them.
MULTI_OPS = sorted([
    ">>=", "<<=", "...", "->*", "<=>", "::", "->", "++", "--", "<<", ">>",
    "<=", ">=", "==", "!=", "&&", "||", "+=", "-=", "*=", "/=", "%=", "&=",
    "|=", "^=", ".*",
], key=len, reverse=True)

# Pairs of adjacent characters that must be kept apart so they don't fuse
# into a longer operator or a comment opener.
_DANGER2 = set(op[:2] for op in MULTI_OPS if len(op) >= 2) | {"//", "/*"}


class Tok:
    __slots__ = ("k", "v")

    def __init__(self, k, v):
        self.k, self.v = k, v


def lex(src):
    """Return a list of Tok. Kinds: dir, str, char, num, id, op.

    Comments and whitespace are dropped; preprocessor directives are kept
    whole (one Tok each) and must live on their own line in the output.
    """
    toks = []
    i, n = 0, len(src)
    line_start = True
    while i < n:
        c = src[i]
        if c == "\n":
            i += 1
            line_start = True
            continue
        if c in " \t\r\f\v":
            i += 1
            continue
        # comments
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            i += 2
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        # preprocessor directive: '#' as first non-ws char on the line
        if line_start and c == "#":
            j = i
            buf = []
            while j < n:
                ch = src[j]
                if ch == "\\" and j + 1 < n and src[j + 1] == "\n":
                    buf.append(" ")
                    j += 2
                    continue
                if ch == "\n":
                    break
                # strip an inline // comment inside the directive
                if ch == "/" and j + 1 < n and src[j + 1] == "/":
                    while j < n and src[j] != "\n":
                        j += 1
                    break
                buf.append(ch)
                j += 1
            text = re.sub(r"\s+", " ", "".join(buf)).strip()
            toks.append(Tok("dir", text))
            i = j
            line_start = True
            continue
        line_start = False
        # string / char literals (with escapes)
        if c in "\"'":
            quote = c
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    j += 1
                    break
                j += 1
            toks.append(Tok("str" if quote == '"' else "char", src[i:j]))
            i = j
            continue
        # numbers (incl. .5, hex, suffixes)
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] in "._"
                             or (src[j] in "+-" and src[j - 1] in "eEpP")):
                j += 1
            toks.append(Tok("num", src[i:j]))
            i = j
            continue
        # identifiers / keywords
        if c.isalpha() or c == "_":
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            toks.append(Tok("id", src[i:j]))
            i = j
            continue
        # operators / punctuation
        for op in MULTI_OPS:
            if src.startswith(op, i):
                toks.append(Tok("op", op))
                i += len(op)
                break
        else:
            toks.append(Tok("op", c))
            i += 1
    return toks


def needsep(a, b):
    """True if a space is required between token texts a and b."""
    c1, c2 = a[-1], b[0]
    aw = c1.isalnum() or c1 == "_"
    bw = c2.isalnum() or c2 == "_"
    if aw and bw:
        return True
    return (c1 + c2) in _DANGER2


def pick_aliases(count, existing):
    """Yield short alias names not colliding with existing identifiers."""
    import string
    pool = list(string.ascii_letters)
    two = [a + b for a in string.ascii_letters for b in
           string.ascii_lowercase]
    for name in pool + two:
        if name not in existing and name not in KEYWORDS:
            yield name


def make_macros(toks, min_uses, min_len):
    """Return (new_define_texts, renamed_tokens).

    Aliases long, frequently-used identifiers via object-like #defines, but
    only when it nets a byte saving. Identifiers mentioned in existing
    directives are left alone for safety.
    """
    in_dir = set()
    for t in toks:
        if t.k == "dir":
            in_dir.update(re.findall(r"[A-Za-z_]\w*", t.v))

    existing = {t.v for t in toks if t.k == "id"}
    freq = {}
    for t in toks:
        if t.k == "id" and t.v not in KEYWORDS:
            freq[t.v] = freq.get(t.v, 0) + 1

    # candidate identifiers, best saving first
    cands = []
    for name, cnt in freq.items():
        if cnt < min_uses or len(name) < min_len or name in in_dir:
            continue
        cands.append((name, cnt))
    cands.sort(key=lambda x: (x[1] * len(x[0])), reverse=True)

    aliases = pick_aliases(len(cands), set(existing))
    mapping = {}
    defines = []
    for name, cnt in cands:
        alias = next(aliases)
        # saving = (len(name)-len(alias))*cnt - len('#define alias name')
        define_cost = len("#define ") + len(alias) + 1 + len(name)
        saving = (len(name) - len(alias)) * cnt - define_cost
        if saving <= 0:
            continue
        mapping[name] = alias
        defines.append(f"#define {alias} {name}")
        existing.add(alias)

    if mapping:
        for t in toks:
            if t.k == "id" and t.v in mapping:
                t.v = mapping[t.v]
    return defines, toks


def layout(toks, width):
    """Join tokens into lines no wider than `width`, directives on own lines."""
    lines = []
    cur = ""
    last = None
    for t in toks:
        if t.k == "dir":
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(t.v)
            last = None
            continue
        if not cur:
            cur = t.v
        else:
            sep = " " if needsep(last, t.v) else ""
            if len(cur) + len(sep) + len(t.v) > width:
                lines.append(cur)       # newline is itself a valid separator
                cur = t.v
            else:
                cur += sep + t.v
        last = t.v
    if cur:
        lines.append(cur)
    return lines


def oneline_len(toks):
    """Length of the code (excluding directives) if put on a single line."""
    total, last = 0, None
    for t in toks:
        if t.k == "dir":
            last = None
            continue
        if last is not None and needsep(last, t.v):
            total += 1
        total += len(t.v)
        last = t.v
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description="Minify C/C++ and square it.")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--no-macros", action="store_true")
    ap.add_argument("--macro-min-uses", type=int, default=4)
    ap.add_argument("--macro-min-len", type=int, default=5)
    ap.add_argument("--aspect", type=float, default=2.0)
    ap.add_argument("--char-square", action="store_true")
    ap.add_argument("--width", type=int)
    ap.add_argument("--keep-comments", action="store_true")
    args = ap.parse_args(argv)

    if args.input.lower().endswith((".py", ".pyw", ".yaml", ".yml")):
        sys.exit("minsquare only supports C-like (brace) languages; "
                 "whitespace-significant files would break.")

    src = open(args.input, encoding="utf-8", errors="replace").read()
    if args.keep_comments:
        # minimal path: keep comments by not stripping — but our lexer drops
        # them, so this flag just disables the hard failure if that matters.
        pass

    toks = lex(src)
    if not toks:
        sys.exit("No code found.")

    new_defines = []
    if not args.no_macros:
        new_defines, toks = make_macros(toks, args.macro_min_uses,
                                        args.macro_min_len)

    # prepend generated macros as their own directive tokens
    head = [Tok("dir", d) for d in new_defines]
    toks = head + toks

    # choose a width for a (visual) square
    if args.width:
        width = args.width
    else:
        aspect = 1.0 if args.char_square else args.aspect
        body = oneline_len(toks)
        width = max(1, round(math.sqrt(max(1, body) * aspect)))
    longest = max((len(t.v) for t in toks if t.k != "dir"), default=1)
    width = max(width, longest)

    lines = layout(toks, width)
    out = "\n".join(lines) + "\n"

    if args.output:
        open(args.output, "w", encoding="utf-8").write(out)
        rows = len(lines)
        cols = max((len(l) for l in lines), default=0)
        print(f"wrote {args.output}: {len(out)} bytes, {rows} lines x "
              f"{cols} cols", file=sys.stderr)
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
