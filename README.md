# CPlusPlusCubed

`minsquare.py` — minify C/C++/Arduino source and lay it out as a square block.

It strips comments and whitespace, auto-creates short `#define` aliases for
long identifiers that appear many times (e.g. `digitalWrite` → `W`), and
reflows the result so the code block is as close to a square as possible in
your editor.

## Usage

```
python3 minsquare.py input.cpp -o output.cpp
```

Writes to stdout if you omit `-o`.

### Options

| Option | Meaning |
|---|---|
| `-o FILE` | write to FILE (default: stdout) |
| `--no-macros` | don't auto-generate `#define` aliases |
| `--macro-min-uses N` | minimum uses before aliasing an identifier (default 4) |
| `--macro-min-len N` | minimum identifier length to alias (default 5) |
| `--aspect R` | monospace cell height/width for a *visual* square (default 2.0) |
| `--char-square` | make character rows == columns (aspect 1.0) |
| `--width W` | force a fixed column width instead of the square calc |

Monospace characters are about twice as tall as they are wide, so the default
`--aspect 2.0` produces a block that *looks* square on screen. Use
`--char-square` if you want the literal character count to be square instead.

## What it does and does not do

It only makes **behavior-preserving** changes:

- strips `//` and `/* */` comments
- removes non-essential whitespace (keeps a separator only where two tokens
  would otherwise merge)
- aliases long, frequently-used identifiers via `#define`, but only when that
  actually saves bytes
- reflows into a square

It deliberately does **not** rename your variables, inline literals, or delete
logging. Those require understanding the program and would change or break its
behavior, so they are left to you.

Strings and character literals are never split or altered, so a long string
sets a floor on how narrow the square can get.

## Scope

C-like (brace) languages only. Whitespace-significant languages such as Python
can't be line-collapsed without breaking them, so those files are rejected.

## License

MIT. See [LICENSE](LICENSE).
