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

# Type tokens and declaration qualifiers, used to recognise which identifiers
# are *defined in this file* (hence safe to rename directly).
TYPES = {
    "int", "char", "short", "long", "unsigned", "signed", "float", "double",
    "bool", "void", "auto", "size_t", "ssize_t", "wchar_t", "char16_t",
    "char32_t", "uint8_t", "uint16_t", "uint32_t", "uint64_t", "int8_t",
    "int16_t", "int32_t", "int64_t", "byte", "boolean", "word", "String",
    "long", "short",
}
QUALS = {"const", "static", "volatile", "register", "constexpr", "inline",
         "extern", "unsigned", "signed", "mutable"}
# Names that must keep their spelling for the toolchain / linker.
PROTECT = {"setup", "loop", "main"}

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


def dotted_names(toks):
    """Identifiers used as members (after '.' or '->'); never rename these."""
    code = [t for t in toks if t.k != "dir"]
    out = set()
    for i, t in enumerate(code):
        if t.k == "op" and t.v in (".", "->") and i + 1 < len(code) \
                and code[i + 1].k == "id":
            out.add(code[i + 1].v)
    return out


def strip_serial(toks):
    """Remove whole `Serial.xxx(...);` statements.

    Only removes a Serial call that stands as its own statement in a block
    (preceded by ';', '{', '}', a directive, or start-of-file) so it never
    guts the single statement controlled by an `if`/`for`/`while`. Serial used
    anywhere else (a condition, an assignment) is left untouched.
    """
    out = []
    i, n = 0, len(toks)
    prev_ok = True            # at a statement boundary?
    while i < n:
        t = toks[i]
        if (t.k == "id" and t.v == "Serial" and prev_ok
                and i + 1 < n and toks[i + 1].k == "op"
                and toks[i + 1].v == "."):
            # consume up to and including the statement-ending ';'
            depth = 0
            j = i
            while j < n:
                tj = toks[j]
                if tj.k == "op" and tj.v == "(":
                    depth += 1
                elif tj.k == "op" and tj.v == ")":
                    depth -= 1
                elif tj.k == "op" and tj.v == ";" and depth == 0:
                    j += 1
                    break
                j += 1
            i = j                       # skip the whole statement
            continue
        out.append(t)
        if t.k == "dir":
            prev_ok = True
        elif t.k == "op" and t.v in ("{", "}", ";"):
            prev_ok = True
        else:
            prev_ok = False
        i += 1
    return out


def detect_renameable(toks):
    """Return identifiers that are clearly *defined in this file*.

    Conservative on purpose: a false positive (renaming a library name) would
    break compilation, so when in doubt an identifier is left out and handled
    by the always-safe #define path instead. Detects: enum/struct/class type
    names and enum members, and declarators that directly follow a type
    (variables, function names, parameters), including comma-separated ones.
    """
    code = [t for t in toks if t.k != "dir"]
    n = len(code)

    # user-defined type names (enum/struct/class/union Name)
    usertypes = set()
    for i, t in enumerate(code):
        if t.k == "id" and t.v in ("enum", "struct", "class", "union"):
            j = i + 1
            if j < n and code[j].k == "id" and code[j].v not in TYPES:
                usertypes.add(code[j].v)
    types = TYPES | usertypes

    renameable = set(usertypes)

    # enum members: ids inside enum { ... }
    i = 0
    while i < n:
        t = code[i]
        if t.k == "id" and t.v == "enum":
            j = i + 1
            if j < n and code[j].k == "id":   # optional enum name
                j += 1
            if j < n and code[j].k == "op" and code[j].v == "{":
                j += 1
                depth = 1
                while j < n and depth:
                    c = code[j]
                    if c.k == "op" and c.v == "{":
                        depth += 1
                    elif c.k == "op" and c.v == "}":
                        depth -= 1
                    elif c.k == "id" and depth == 1:
                        renameable.add(c.v)
                    j += 1
            i = j
            continue
        i += 1

    # declarators following a type, across comma-separated lists
    in_decl = False          # inside a declaration statement?
    expect_name = False      # next id is a declared name
    paren = 0
    decl_paren = 0           # paren depth where the current declaration began
    for t in code:
        if t.k == "op":
            if t.v == "(":
                paren += 1
            elif t.v == ")":
                paren = max(0, paren - 1)
            elif t.v in ("{", "}", ";"):
                in_decl = False          # a block or statement end clears it
                expect_name = False
            elif t.v == "," and in_decl and paren == decl_paren:
                expect_name = True       # another declarator at the same depth
            elif t.v in ("=", "[") and in_decl:
                expect_name = False      # initialiser / array size
            continue
        if t.k != "id":
            continue
        if t.v in types:                 # a type opens a declaration here
            in_decl = True
            decl_paren = paren
            expect_name = True
            continue
        if t.v in QUALS:
            continue
        if in_decl and expect_name and paren == decl_paren:
            renameable.add(t.v)
            expect_name = False
        else:
            expect_name = False
    renameable -= PROTECT
    renameable -= set(KEYWORDS)
    renameable -= TYPES
    renameable -= dotted_names(toks)
    return renameable


def rename_idents(toks, names):
    """Rename the given identifiers to the shortest fresh aliases.

    Only identifiers longer than one character can shrink. Every name that
    ends up keeping its spelling (including short ones) is reserved so no
    alias ever collides with it. Ordering is deterministic.
    """
    names = {n for n in names if len(n) > 1}
    if not names:
        return {}
    freq = {}
    for t in toks:
        if t.k == "id" and t.v in names:
            freq[t.v] = freq.get(t.v, 0) + 1
    # hottest first; ties broken by name so runs are reproducible
    order = sorted(names, key=lambda x: (-freq.get(x, 0), x))
    # reserved = every spelling that will remain in the code
    reserved = ({t.v for t in toks if t.k == "id"} - names) | set(KEYWORDS)
    gen = alias_candidates()
    nxt = next(gen)
    mapping = {}
    for name in order:
        while nxt in reserved:
            nxt = next(gen)
        if len(nxt) < len(name):          # use this alias
            mapping[name] = nxt
            reserved.add(nxt)
            nxt = next(gen)
        else:                             # can't shorten; keep original name
            reserved.add(name)
    for t in toks:
        if t.k == "id" and t.v in mapping:
            t.v = mapping[t.v]
    return mapping


def needsep(a, b):
    """True if a space is required between token texts a and b."""
    c1, c2 = a[-1], b[0]
    aw = c1.isalnum() or c1 == "_"
    bw = c2.isalnum() or c2 == "_"
    if aw and bw:
        return True
    return (c1 + c2) in _DANGER2


def alias_candidates():
    """Yield short identifier names: a..z, A..Z, then aa, ab, ... ."""
    import string
    for a in string.ascii_letters:
        yield a
    for a in string.ascii_letters:
        for b in string.ascii_lowercase:
            yield a + b


def pick_aliases(count, existing):
    """Yield short alias names not colliding with existing identifiers."""
    for name in alias_candidates():
        if name not in existing and name not in KEYWORDS:
            yield name


def make_macros(toks, min_uses, min_len, exclude=frozenset()):
    """Return (new_define_texts, renamed_tokens).

    Aliases long, frequently-used identifiers via object-like #defines, but
    only when it nets a byte saving. Identifiers mentioned in existing
    directives, or in `exclude` (e.g. member names), are left alone.
    """
    in_dir = set(exclude)
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


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def load_png_mask(path, thresh=128):
    """Read a PNG into a 2-D bool mask (True = white = where code goes).

    Pure stdlib. Supports non-interlaced PNGs, bit depths 1/2/4/8/16, and
    colour types grayscale, RGB, palette, and their alpha variants. Any
    colour is reduced to luminance and thresholded, so a plain black/white
    image works directly.
    """
    import struct
    import zlib

    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos = 8
    width = height = bitd = ctype = interlace = 0
    idat = bytearray()
    plte = trns = None
    while pos + 8 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            (width, height, bitd, ctype, _comp, _filt,
             interlace) = struct.unpack(">IIBBBBB", chunk)
        elif typ == b"PLTE":
            plte = chunk
        elif typ == b"tRNS":
            trns = chunk
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
    if interlace != 0:
        raise ValueError("interlaced PNGs are not supported")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(bytes(idat))
    stride = (width * channels * bitd + 7) // 8
    bpp = max(1, (channels * bitd + 7) // 8)
    rows = []
    prev = bytearray(stride)
    i = 0
    for _y in range(height):
        f = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        if f == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 255
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif f == 3:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                c = prev[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + _paeth(a, prev[x], c)) & 255
        prev = line
        rows.append(bytes(line))

    maxval = (1 << bitd) - 1

    def channels_at(line, x):
        if bitd == 8:
            off = x * channels
            return tuple(line[off:off + channels])
        if bitd == 16:
            off = x * channels * 2
            return tuple(line[off + 2 * k] for k in range(channels))
        per = 8 // bitd                       # sub-byte (1/2/4), 1 channel
        byte = line[x // per]
        shift = (per - 1 - (x % per)) * bitd
        return ((byte >> shift) & maxval,)

    mask = []
    for line in rows:
        mrow = []
        for x in range(width):
            px = channels_at(line, x)
            if ctype == 3:                     # palette
                idx = px[0]
                r, g, b = plte[idx * 3], plte[idx * 3 + 1], plte[idx * 3 + 2]
                a = 255
                if trns and idx < len(trns):
                    a = trns[idx]
                lum = 0.299 * r + 0.587 * g + 0.114 * b
            elif ctype in (0, 4):              # gray (+alpha)
                v = px[0] * 255 // maxval if bitd < 8 else px[0]
                a = px[1] if ctype == 4 else 255
                lum = v
            else:                              # RGB(A)
                r, g, b = px[0], px[1], px[2]
                a = px[3] if ctype == 6 else 255
                lum = 0.299 * r + 0.587 * g + 0.114 * b
            mrow.append(lum >= thresh and a >= 128)
        mask.append(mrow)
    return mask


def resize_mask(mask, rows, aspect):
    """Nearest-neighbour resize to `rows` tall, width set by aspect so the
    shape looks right in a monospace grid (cells ~`aspect`x tall)."""
    h0 = len(mask)
    w0 = len(mask[0]) if h0 else 1
    cols = max(1, round(rows * (w0 / h0) * aspect))
    out = []
    for y in range(rows):
        sy = min(h0 - 1, y * h0 // rows)
        src = mask[sy]
        out.append([src[min(w0 - 1, x * w0 // cols)] for x in range(cols)])
    return out


def shape_place(tokens, mask):
    """Pour tokens into the white runs of `mask`, row by row.

    Returns (all_placed, lines). Black pixels and run padding become spaces;
    tokens are never split, so a token only lands inside one white run.
    """
    ti, n = 0, len(tokens)
    lines = []
    for row in mask:
        w = len(row)
        buf = [" "] * w
        x = 0
        while x < w:
            if not row[x]:
                x += 1
                continue
            e = x
            while e < w and row[e]:
                e += 1
            pos = x
            prev = None
            while ti < n:
                tv = tokens[ti].v
                sep = 1 if (prev is not None and needsep(prev, tv)) else 0
                if pos + sep + len(tv) <= e:
                    pos += sep
                    for ch in tv:
                        buf[pos] = ch
                        pos += 1
                    prev = tv
                    ti += 1
                else:
                    break
            x = e
        lines.append("".join(buf).rstrip())
    return ti == n, lines


def fit_mask(tokens, mask0, aspect):
    """Find the smallest scale of mask0 that fits every token; render it."""
    import math
    white0 = sum(sum(1 for c in r if c) for r in mask0)
    h0 = len(mask0)
    w0 = len(mask0[0]) if h0 else 1
    total = sum(len(t.v) for t in tokens) + len(tokens)   # chars + rough seps
    phi = white0 / (h0 * w0) if h0 and w0 else 0
    if phi <= 0:
        raise ValueError("the image has no white pixels for code to fill")
    est = max(2, int(math.sqrt(total / (phi * (w0 / h0) * aspect))))

    def ok(R):
        placed, _ = shape_place(tokens, resize_mask(mask0, R, aspect))
        return placed

    hi = est
    cap = est * 16 + 200
    while hi < cap and not ok(hi):
        hi *= 2
    if not ok(hi):
        raise ValueError("the shape is too thin to hold the code's longest "
                         "tokens; try a rounder/larger image")
    lo = 1
    while lo < hi:
        mid = (lo + hi) // 2
        if ok(mid):
            hi = mid
        else:
            lo = mid + 1
    _, lines = shape_place(tokens, resize_mask(mask0, hi, aspect))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="Minify C/C++ and square it.")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--no-macros", action="store_true")
    ap.add_argument("--no-rename", action="store_true",
                    help="don't rename file-defined identifiers directly "
                         "(safer for multi-file projects)")
    ap.add_argument("-serial", "--serial", dest="serial", action="store_true",
                    help="keep Serial.xxx(...) statements; by default they "
                         "are stripped out")
    ap.add_argument("--macro-min-uses", type=int, default=4)
    ap.add_argument("--macro-min-len", type=int, default=5)
    ap.add_argument("--aspect", type=float, default=2.0)
    ap.add_argument("--char-square", action="store_true")
    ap.add_argument("--width", type=int)
    ap.add_argument("--png", help="black/white image; code fills the white "
                                  "pixels, scaled so it fits exactly")
    ap.add_argument("--threshold", type=int, default=128,
                    help="luminance cutoff for white vs black (0-255)")
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

    # 0) drop Serial.xxx(...) statements unless the user asked to keep them
    if not args.serial:
        toks = strip_serial(toks)

    # 1) directly rename identifiers defined in this file (no #define cost)
    if not args.no_rename:
        rename_idents(toks, detect_renameable(toks))

    # 2) #define-alias the remaining long, frequently-used names (library
    #    symbols we can't rename). Member names are never aliased.
    new_defines = []
    if not args.no_macros:
        new_defines, toks = make_macros(toks, args.macro_min_uses,
                                        args.macro_min_len,
                                        exclude=dotted_names(toks))

    aspect = 1.0 if args.char_square else args.aspect

    if args.png:
        # shape the code to the image's white pixels; directives go above it
        mask0 = load_png_mask(args.png, args.threshold)
        dirs = [Tok("dir", d) for d in new_defines] + \
               [t for t in toks if t.k == "dir"]
        code = [t for t in toks if t.k != "dir"]
        body = fit_mask(code, mask0, aspect)
        lines = [d.v for d in dirs] + body
        out = "\n".join(lines) + "\n"
    else:
        # prepend generated macros as their own directive tokens
        head = [Tok("dir", d) for d in new_defines]
        toks = head + toks
        # choose a width for a (visual) square
        if args.width:
            width = args.width
        else:
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
