# CPlusPlusCubed

`minsquare.py` — minify C/C++/Arduino source into the smallest compilable code
it safely can, laid out as a square block.

It strips comments and whitespace, renames your own identifiers to short names,
aliases library calls with `#define` where that saves bytes, optionally removes
`Serial` logging, and reflows the result so the code block is as close to a
square as possible in your editor.

## Usage

```
python3 minsquare.py input.cpp -o output.cpp
```

Writes to stdout if you omit `-o`.

### Options

| Option | Meaning |
|---|---|
| `-o FILE` | write to FILE (default: stdout) |
| `-serial`, `--serial` | **keep** `Serial.xxx(...)` statements (by default they are stripped) |
| `--no-rename` | don't rename file-defined identifiers directly (safer for multi-file projects) |
| `--no-macros` | don't auto-generate `#define` aliases |
| `--macro-min-uses N` | minimum uses before aliasing an identifier (default 4) |
| `--macro-min-len N` | minimum identifier length to alias (default 5) |
| `--aspect R` | monospace cell height/width for a *visual* square (default 2.0) |
| `--char-square` | make character rows == columns (aspect 1.0) |
| `--width W` | force a fixed column width instead of the square calc |
| `--png FILE` | shape the code to a black/white image's white pixels |
| `--threshold N` | luminance cutoff for white vs black, 0–255 (default 128) |

## Shaping to an image

Pass a black-and-white PNG and the code is poured into its **white** pixels,
black pixels become empty space:

```
python3 minsquare.py sketch.ino --png circle.png -o out.cpp
```

The image can be any size and any colours (it's reduced to luminance and
thresholded). The tool automatically scales the shape so the white area holds
the code exactly — all of it fits and little is left empty. Tokens are never
split, so the result still compiles. Any `#define` lines sit above the shape.

With no `--png`, the layout is a square, as described above.

Monospace characters are about twice as tall as they are wide, so the default
`--aspect 2.0` produces a block that *looks* square on screen. Use
`--char-square` if you want the literal character count to be square instead.

### Serial

By default, whole `Serial.xxx(...)` statements (`begin`, `print`, `println`,
…) are removed — handy for Arduino sketches where the logging is only for
debugging. Pass `-serial` to keep them:

```
python3 minsquare.py sketch.ino            # Serial stripped (smallest)
python3 minsquare.py sketch.ino -serial    # Serial kept
```

A `Serial` call is only removed when it stands as its own statement in a block.
One that is the single body of an `if`/`for`/`while`, or used in a condition,
is left alone so control flow never breaks.

## How it compresses

In order, the tool:

1. **Strips Serial** statements (unless `-serial`).
2. **Renames identifiers defined in this file** — your variables, functions,
   enum members — directly to short names, with no `#define` overhead. Names
   that come from a library (`digitalWrite`, `millis`, `Serial`, …) are *not*
   renamed, because you don't own those symbols.
3. **`#define`-aliases** the remaining long, frequently-used library names,
   but only when it nets a byte saving (e.g. `digitalWrite` → `W`).
4. **Reflows** everything into a square.

Comments and whitespace are removed throughout. Strings and character literals
are never split or altered, so a long string sets a floor on how narrow the
square can get.

## Examples

```bash
# smallest compilable output, square, Serial logging stripped
python3 minsquare.py sketch.ino -o sketch.min.cpp

# keep Serial logging
python3 minsquare.py sketch.ino -serial -o sketch.min.cpp

# shape the code to the white pixels of an image
python3 minsquare.py sketch.ino --png circle.png -o sketch.shaped.cpp

# multi-file project: only use reversible #define aliases, never rename
python3 minsquare.py part.cpp --no-rename -o part.min.cpp

# print to the terminal instead of a file
python3 minsquare.py sketch.ino
```

## Safety and limits

- Direct renaming assumes a **single self-contained translation unit**. On
  multi-file projects an identifier defined here may be referenced elsewhere,
  so use `--no-rename` (the tool then only uses reversible `#define` aliases).
- The rename detector is conservative: when it can't be sure an identifier is
  defined in the file, it leaves it alone and falls back to `#define`.
- `setup`, `loop`, and `main` are never renamed.
- C-like (brace) languages only. Whitespace-significant languages such as
  Python can't be line-collapsed without breaking them, so those files are
  rejected.

## License

MIT. See [LICENSE](LICENSE).
