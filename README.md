# CPlusPlusCubed

**Pour your C/C++/Arduino code into a picture.** Give `minsquare.py` a
black-and-white image and it lays your code out across the image's white
pixels — in any shape — while keeping it compilable. With no image, it falls
back to a neat square.

A short Arduino sketch poured into a heart:

```
       int e=9     ;int d=
    2;int a=0;bool b=true;
   const int c=5;void setup(){
   pinMode(e,OUTPUT);pinMode(d
  ,INPUT_PULLUP);}void loop(){
   if(digitalRead(d)==LOW){a=0
    ;b=true;}analogWrite(e,a)
     ;if(b){a+=c;if(a>=255)b
      =false;}else{a-=c;if
        (a<=0)b=true;}
          delay(15);}
```

That still compiles. To get there, the tool also minifies: it strips comments
and whitespace, renames your identifiers to short names, aliases library calls
with `#define` where that saves bytes, and (by default) removes `Serial`
logging.

## Shape your code to an image

```
python3 minsquare.py sketch.ino --png heart.png -o sketch.cpp
```

- **White pixels get code, black pixels become empty space.** A black shape on
  a white background becomes a hole in a block of code; a white shape on black
  becomes a solid shape made of code.
- The image can be **any size and any colours** — it's reduced to luminance and
  thresholded (`--threshold N`, 0–255).
- The tool **auto-scales the shape** so the white area holds your code exactly:
  all of it fits, with little left empty.
- **Tokens are never split** across the shape, so the output still compiles.
  Any `#define` lines sit above the shape.

### How it works

Your minified code is a stream of tokens. The tool walks the image row by row,
dropping whole tokens into each run of white pixels and filling the rest with
spaces. Because black pixels and line breaks only ever land *between* tokens
(never inside one), they act as ordinary whitespace and the code compiles
unchanged. A binary search finds the smallest scale of the image whose white
area still fits every token.

## Square mode (no image)

With no `--png`, the code is laid out as a block that's as close to a square as
possible. Monospace characters are about twice as tall as they are wide, so the
default `--aspect 2.0` makes it look square on screen; `--char-square` makes the
literal character count square instead.

```
#define x digitalWrite
#define y millis
const int b=13;const int l=2;const
unsigned long p=50;const unsigned long
q=1000;const unsigned long o=500;int i=
HIGH;int f=HIGH;...
```

## Usage

```
python3 minsquare.py input.cpp -o output.cpp
```

Writes to stdout if you omit `-o`.

| Option | Meaning |
|---|---|
| `--png FILE` | shape the code to a black/white image's white pixels |
| `--threshold N` | luminance cutoff for white vs black, 0–255 (default 128) |
| `-o FILE` | write to FILE (default: stdout) |
| `-serial`, `--serial` | **keep** `Serial.xxx(...)` statements (stripped by default) |
| `--no-rename` | don't rename file-defined identifiers (safer for multi-file projects) |
| `--no-macros` | don't auto-generate `#define` aliases |
| `--macro-min-uses N` | minimum uses before aliasing an identifier (default 4) |
| `--macro-min-len N` | minimum identifier length to alias (default 5) |
| `--aspect R` | monospace cell height/width for a visual square (default 2.0) |
| `--char-square` | make character rows == columns (aspect 1.0) |
| `--width W` | force a fixed column width in square mode |

## Examples

```bash
# pour the code into a shape
python3 minsquare.py sketch.ino --png circle.png -o shaped.cpp

# square, smallest output, Serial logging stripped
python3 minsquare.py sketch.ino -o sketch.min.cpp

# keep Serial logging
python3 minsquare.py sketch.ino -serial -o sketch.min.cpp

# multi-file project: only reversible #define aliases, never rename
python3 minsquare.py part.cpp --no-rename -o part.min.cpp
```

## Serial

By default, whole `Serial.xxx(...)` statements (`begin`, `print`, `println`, …)
are removed — handy for Arduino sketches where logging is only for debugging.
Pass `-serial` to keep them. A Serial call is only removed when it stands as
its own statement in a block; one that is the single body of an `if`/`for`/
`while`, or used in a condition, is left alone so control flow never breaks.

## How it compresses

In order, the tool:

1. **Strips Serial** statements (unless `-serial`).
2. **Renames identifiers defined in this file** — your variables, functions,
   enum members — directly to short names, with no `#define` overhead. Library
   names (`digitalWrite`, `millis`, …) are not renamed, because you don't own
   those symbols.
3. **`#define`-aliases** the remaining long, frequently-used library names, but
   only when it nets a byte saving (e.g. `digitalWrite` → `W`).
4. **Shapes** the result into the image, or into a square.

Comments and whitespace are removed throughout. Strings and character literals
are never split or altered, so a long string sets a floor on how narrow the
shape can get.

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
- The PNG reader is pure Python standard library (no Pillow): non-interlaced
  PNGs, bit depths 1/2/4/8/16, grayscale / RGB / palette and their alpha
  variants.

## License

MIT. See [LICENSE](LICENSE).
