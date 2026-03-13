# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Domain Background: 6-Piece Burr Puzzles

A **6-piece burr** is a mechanical puzzle where six notched rectangular prisms interlock into a 3D cross shape. Each piece occupies one of three orthogonal axes (two pieces per axis: X, Y, Z).

### Piece Geometry and Numbering

Each piece is a 2×2×6 voxel grid. The end cubes at x=0 and x=5 are always solid. The core region (x=1..4) contains **12 removable cubies** — the only positions that can be notched without creating exterior holes.

The **piece ID** (von Känel / Cutler numbering): `piece_ID = bitmap + 1`, where `bitmap` has a bit set for each *absent* cubie. The solid piece (no notches) is ID 1; each removed cubie increases the ID. IDs range 1–4096. Of the 4,096 possible bitmaps, **2,225 produce connected pieces**; those 2,225 orientations correspond to **837 distinct physical pieces** (up to 8 orientations per piece under rotation).

The 12 removable positions are mapped to bits 0–11 in `normalize.py:BIT_MAP`:
- Bits 0–3: top-back row (x=1..4, y=1, z=1)
- Bits 4–7: top-front row (x=1..4, y=0, z=1)
- Bits 8–9: bottom-back pair (x=2..3, y=1, z=0)
- Bits 10–11: bottom-front pair (x=2..3, y=0, z=0)

The four bottom-layer corner positions (x=1,4 × y=0,1 at z=0) are always solid — they are `FIXED_POSITIONS` and not in the bitmap.

**Piece weight** = 12 − (removed cubies) = core cubies present. Ranges 2–12.

### Piece Categories

**Notchable** (59 pieces): Every cross-section perpendicular to the long axis is convex — all saw cuts are straight passes across X. No L-shapes or recessed cross-sections.

**Millable** (78 pieces, superset of notchable): No **internal corners** (concave pockets where three cube faces meet inside the piece). Can be made with a milling machine.

**General** (837 pieces): All connected pieces, including those with internal corners that require chisel or cube-gluing construction.

The "32" suffix denotes pieces usable in solid (weight-32) burrs: notchable32=25, millable32=78, general32=369.

### The Assembled Burr Shape and Weight

Six assembled pieces occupy a **6×6×6 bounding box** with **32 interior cube positions** (where piece slots overlap). Each interior position is either occupied by a cubie or is an empty **hole (void)**.

**Burr weight** = sum of the 6 piece weights = occupied interior positions. Maximum = 32 (solid burr).

**Key assembly constraint**: If the sum of the 6 piece weights exceeds 32, the pieces cannot be assembled. Equivalently, total removed cubies must be ≥ 40 (= 6×12 − 32). This is what `normalize.py --strict` (`MIN_REMOVED_CUBIES = 40`) enforces.

- **Solid burr**: Weight = 32, zero holes. 119,979 solid assemblies exist (314 notchable-only). All solid burrs are level 1.
- **Holey burr**: 1–20 holes. Holes enable high-level disassembly. Cutler's complete analysis found ~5.95 billion solvable assemblies out of ~35.65 billion total.

**Fitable vs Solvable**: A puzzle is **fitable** if the pieces can be assembled into the shape. It is **solvable** if it can also be physically taken apart by linear moves only (no twisting). Some fitable assemblies are not solvable.

### Level

The **level** of a solution is the minimum number of moves to remove the first piece or group. Move counting rules (IBM/Cutler Def 11.0):
- A move = any distance in one direction; distance doesn't matter, only direction changes.
- Changing direction = new move.
- Multiple pieces moving independently in the same direction back-to-back = one move.
- The final removal step is counted as a move.

After the first separation, each sub-group has its own sub-level. **BurrTools' dot-notation** (e.g., `"5.1.1.1.1"`) records moves at each node of the separation tree. This is what `flatten_separation_tree()` in `solve.py` converts to keyframes.

Notable benchmarks: solid burrs are all level 1; notchable unique solutions top out at level 5 (139 such puzzles); the highest known unique-solution level is 10 ("Computer's Choice Unique 10", 18 assemblies); the absolute maximum is level 12 ("Love's Dozen", non-unique, only one exists).

### Symmetry and Canonical Form

A piece has 8 symmetries within its 2×2×6 skeleton: 4 rotations around the long (X) axis × 2 end-to-end flips. `canonical_id()` in `normalize.py` finds the lowest-numbered ID among all 8 equivalents. Normalizing a puzzle replaces each piece with its canonical ID and sorts the 6 IDs ascending, giving a unique representation for each physically distinct puzzle.

### Primary References

- **IBM Research BurrPuzzles** (Jürg von Känel et al., ~1997) — formal definitions of piece numbering, notchable/millable/level/weight. Downloaded pages in `context/web.archive.org/`.
- **Bill Cutler, "A Computer Analysis of All 6-Piece Burrs"** (billcutlerpuzzles.com/docs/CA6PB/) — definitive computational analysis; piece numbering and terminology in this codebase follows Cutler/IBM.
- **Rob's Puzzle Page** (robspuzzlepage.com/interlocking.htm) — survey of history and terminology.

## Commands

**Run the animation server (primary UI):**
```bash
python server.py [--port 8765]
# Then open http://localhost:8765 (redirects to anim.html)
```

**Check if a puzzle is solvable:**
```bash
python solve.py 103 205 3175 3322 3324 3326
python solve.py 0x0670CDC67CFACFCCFE   # compact 18-hex puzzle ID
python solve.py -v 1 205 222 3328 3328 3328  # verbose
```

**Normalize a puzzle (canonical form):**
```bash
python normalize.py 65 1 256 154 888 35
python normalize.py -v -s 65 1 256 154 888 35  # verbose + strict cubie check
```

**Build the BurrTools solver (required before first use):**
```bash
cd src/burr-tools && meson setup build && ninja -C build burrTxt2
```

## Architecture

The project is a toolchain for analyzing and visualizing 6-piece burr puzzles. A compact **puzzle ID** packs 6 piece IDs into 18 hex digits (3 hex chars each, e.g. `0x001003023099100378`).

### Python Modules
- **`normalize.py`** — Core library. Defines `BIT_MAP`, piece grid operations (`id_to_grid`, `grid_to_id`, `transform_grid`), `canonical_id` (lowest rotationally-equivalent ID under 8 symmetries), `is_connected` (BFS flood-fill), and puzzle/piece ID parsing. Imported by all other modules.
- **`solve.py`** — Generates `.xmpuzzle` XML (gzip-compressed) and invokes the `burrTxt2` binary. Two entry points: `solve()` returns a simple solvability dict; `solve_full()` returns full assembly placements plus separation-tree keyframes for animation. The solver needs a non-EOF stdin (uses `os.pipe()` to avoid premature abort).
- **`server.py`** — `SimpleHTTPRequestHandler` subclass serving static files with one API endpoint: `POST /api/solve` accepts `{"pieces": "..."}` and returns `solve_full()` JSON.

### Frontend (Three.js, no build step)
- **`viz.html`** — Static piece visualizer. Renders individual pieces or all 6 in assembled position. Contains detailed comments explaining the geometry, coordinate system, and notchability/millability/printability tag definitions.
- **`anim.html`** — Full puzzle animator. Calls `/api/solve`, then animates the disassembly using keyframes from the separation tree. Supports yo-yo playback, manual stepping, multiple solutions, and a preset puzzle dropdown.

### BurrTools Dependency
`src/burr-tools/` is a nested git repo (BurrTools C++ project). Only `burrTxt2` is needed — the solver CLI. The binary is expected at `src/burr-tools/build/burrTxt2` relative to the repo root. `solve.py` also walks up parent directories to find it when running from a git worktree.

### Key Data Flow
```
piece IDs → generate_xmpuzzle() → .xmpuzzle (gzip XML) → burrTxt2 → output XML
         → parse_assembly() + flatten_separation_tree() → keyframes JSON → anim.html
```
