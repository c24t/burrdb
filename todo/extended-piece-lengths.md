# Support extended piece lengths (2x2x8, 2x2x10, 2x2x12)

## Problem

The codebase assumes 2x2x6 pieces throughout: 12 removable cubies, piece
IDs 1-4096, a 6x6x6 assembled shape with 32 interior positions. This
matches the standard von Kanel / Cutler numbering.

However, the **level of a burr changes with piece length** (IBM Def 11.1).
The same notch pattern can produce different disassembly levels at lengths
6, 8, 10, and 12. Cutler's "level type" is a four-hex-digit encoding of
the level at each of these four lengths.

The most notable example: **Computer's Choice Unique 10** achieves level 10
only at piece length 8. At length 6, the same notch pattern yields only
level 5.2. The extra travel distance from the longer pieces is essential
for the 10-move separation sequence.

From the IBM page: "The highest level unique six-piece burr is of level 10
if the pieces are 8 units long and level 9 if the pieces are 6 units in
length."

## What changes at length 8

| Property           | Length 6       | Length 8         |
|--------------------|----------------|------------------|
| Piece grid         | 2x2x6          | 2x2x8            |
| Core region        | x=1..4 (4 wide)| x=1..6 (6 wide)  |
| Removable cubies   | 12             | 20               |
| Max piece ID       | 4096           | 1,048,576 (2^20) |
| Assembled shape    | 6x6x6          | 8x8x8            |
| Interior positions | 32             | 64 (?)           |

## Files that would need changes

- **normalize.py**: `BIT_MAP` has 12 entries for the 12 removable positions.
  Would need to be extended for the wider core region. `FIXED_POSITIONS`
  would change too. `canonical_id()` and all grid functions assume 2x2x6.

- **solve.py**: `generate_xmpuzzle()` hardcodes `x="6" y="2" z="2"` for
  piece shapes and `x="6" y="6" z="6"` for the target. Would need to
  parameterize on piece length.

- **exploded.html** / **anim.html** / **viz.html**: `getPieceVoxels()`
  assumes x=0..5 for a 6-long piece. Rendering, explode distances, and
  coordinate systems would all need updating.

- **server.py**: The `/api/solve` endpoint would need to accept a piece
  length parameter.

## Piece numbering at length 8

The von Kanel/Cutler numbering is specific to length 6. At length 8, the
core region has 20 removable cubies, so piece IDs would range up to 2^20.
The two numbering systems are not directly comparable -- the same physical
notch pattern gets a different ID depending on piece length, because the
bitmap encodes different sets of positions.

The IBM applet and Cutler's BURR6 program both support length 6 and 8
(and 10, 12). BurrTools also supports arbitrary piece dimensions via its
xmpuzzle format.

## Notable puzzles requiring length 8

- **Computer's Choice Unique 10**: level 10 at length 8, level 5 at length 6.
  Pieces (length-6 IDs): 624 702 768 883 1015 1024 (currently in presets
  but only solvable at the lower level).

## References

- IBM Research BurrPuzzles, Def 11.1 ("Level Type"):
  `context/web.archive.org/web/20120606142755fw_/http:/www.research.ibm.com/BurrPuzzles/Burr6.html`
- Bill Cutler, "Computer Analysis of All 6-Piece Burrs", examples section:
  billcutlerpuzzles.com/docs/CA6PB/examples.html
- BURR6 program documentation (supports length 6 or 8):
  billcutlerpuzzles.com/docs/H6PB/program.html
