#!/usr/bin/env python3
"""Normalize a 6-piece burr puzzle description.

Takes 6 piece IDs (1-4096) and normalizes the puzzle by:
  1. Validating all IDs are in range and exactly 6 are given
  2. Rejecting disconnected pieces
  3. Replacing each piece with its lowest rotationally-equivalent ID
  4. Sorting piece IDs ascending

Piece IDs can be decimal, hex (0x prefix), or binary (0b prefix).
A compact puzzle ID (18 hex digits, optional 0x prefix) is also accepted.

Usage:
    python normalize.py 65 1 256 154 888 35
    python normalize.py 0x41 0b1 0x100 154 0x378 35
    python normalize.py 001003023099100378
    python normalize.py 0x001003023099100378
    python normalize.py -v 65 1 256 154 888 35
    python normalize.py -s 1 1 1 1 1 1
"""

import argparse
import sys

# Bit index -> (x, y, z) coordinate in the 6x2x2 grid.
# Bit set = cubie removed. Bit clear = cubie present.
BIT_MAP = [
    (1, 1, 1), (2, 1, 1), (3, 1, 1), (4, 1, 1),  # bits 0-3: Top Back
    (1, 0, 1), (2, 0, 1), (3, 0, 1), (4, 0, 1),  # bits 4-7: Top Front
    (2, 1, 0), (3, 1, 0),                          # bits 8-9: Bottom Back
    (2, 0, 0), (3, 0, 0),                          # bits 10-11: Bottom Front
]

# Positions that are always solid (ends + bottom-layer spine).
# Everything not in BIT_MAP within the 6x2x2 grid.
_BIT_MAP_SET = set(BIT_MAP)
FIXED_POSITIONS = [
    (x, y, z)
    for x in range(6) for y in range(2) for z in range(2)
    if (x, y, z) not in _BIT_MAP_SET
]

# The assembled burr interior has 32 cubie positions.
# 6 pieces x 12 removable cubies = 72 total, so at least 40 must be removed.
MIN_REMOVED_CUBIES = 40


def id_to_grid(piece_id: int) -> list[list[list[bool]]]:
    """Convert piece ID (1-4096) to a 6x2x2 boolean grid. True = solid."""
    bitmap = piece_id - 1
    grid = [[[False] * 2 for _ in range(2)] for _ in range(6)]
    # Fixed positions are always solid
    for x, y, z in FIXED_POSITIONS:
        grid[x][y][z] = True
    # Removable positions: solid unless the corresponding bit is set
    for i, (x, y, z) in enumerate(BIT_MAP):
        grid[x][y][z] = not ((bitmap >> i) & 1)
    return grid


def grid_to_id(grid: list[list[list[bool]]]) -> int | None:
    """Convert a 6x2x2 grid back to a piece ID (1-4096).

    Returns None if mandatory positions are missing (the transform
    produced a shape that doesn't fit the burr piece skeleton).
    """
    # Check mandatory solid positions (the fixed voxels that define the piece skeleton).
    for x, y, z in FIXED_POSITIONS:
        if not grid[x][y][z]:
            return None

    bitmap = 0
    for i, (x, y, z) in enumerate(BIT_MAP):
        if not grid[x][y][z]:
            bitmap |= 1 << i
    return bitmap + 1


def transform_grid(
    src: list[list[list[bool]]], flip_ends: bool, rot_x_steps: int
) -> list[list[list[bool]]]:
    """Apply a symmetry transform: optional 180-degree Z flip + N 90-degree X rotations."""
    dst = [[[False] * 2 for _ in range(2)] for _ in range(6)]
    for x in range(6):
        for y in range(2):
            for z in range(2):
                if not src[x][y][z]:
                    continue
                nx, ny, nz = x, y, z
                if flip_ends:
                    nx = 5 - x
                    ny = 1 - y
                for _ in range(rot_x_steps):
                    ny, nz = nz, 1 - ny
                if 0 <= nx < 6 and 0 <= ny < 2 and 0 <= nz < 2:
                    dst[nx][ny][nz] = True
    return dst


def is_connected(piece_id: int) -> bool:
    """Check whether a piece forms a single connected component (BFS flood fill)."""
    grid = id_to_grid(piece_id)

    # Find all solid voxels and a start node
    solid = set()
    for x in range(6):
        for y in range(2):
            for z in range(2):
                if grid[x][y][z]:
                    solid.add((x, y, z))

    if not solid:
        return False

    # BFS from an arbitrary solid voxel
    start = next(iter(solid))
    visited = {start}
    queue = [start]
    neighbors = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]

    while queue:
        cx, cy, cz = queue.pop()
        for dx, dy, dz in neighbors:
            nx, ny, nz = cx + dx, cy + dy, cz + dz
            if (nx, ny, nz) in solid and (nx, ny, nz) not in visited:
                visited.add((nx, ny, nz))
                queue.append((nx, ny, nz))

    return len(visited) == len(solid)


def canonical_id(piece_id: int) -> int:
    """Find the lowest rotationally-equivalent piece ID.

    Tests 8 symmetries: 2 flips (identity, 180-degree Z) x 4 X-rotations (0/90/180/270).
    """
    grid = id_to_grid(piece_id)
    min_id = piece_id
    for flip in range(2):
        for rot in range(4):
            if flip == 0 and rot == 0:
                continue  # skip identity
            t = transform_grid(grid, flip == 1, rot)
            tid = grid_to_id(t)
            if tid is not None and tid < min_id:
                min_id = tid
    return min_id


def piece_weight(piece_id: int) -> int:
    """Count the removable cubies that are present (not removed)."""
    bitmap = piece_id - 1
    return 12 - bin(bitmap).count("1")


def total_removed(piece_ids: list[int]) -> int:
    """Count total removed cubies across all pieces."""
    return sum(12 - piece_weight(pid) for pid in piece_ids)


def normalize(piece_ids: list[int], verbose: bool = False) -> list[int]:
    """Validate and normalize a list of 6 piece IDs."""
    if len(piece_ids) != 6:
        raise ValueError(f"Expected 6 pieces, got {len(piece_ids)}")

    for pid in piece_ids:
        if not (1 <= pid <= 4096):
            raise ValueError(f"Piece ID {pid} out of range (must be 1-4096)")
        if not is_connected(pid):
            raise ValueError(f"Piece {pid} is disconnected")

    # Replace with canonical IDs
    canonical = []
    for pid in piece_ids:
        cid = canonical_id(pid)
        if verbose and cid != pid:
            print(f"  Piece {pid} -> {cid} (rotational equivalent)", file=sys.stderr)
        canonical.append(cid)

    # Sort ascending
    result = sorted(canonical)
    if verbose and result != canonical:
        print(f"  Sorted: {' '.join(str(p) for p in canonical)} -> {' '.join(str(p) for p in result)}", file=sys.stderr)

    return result


def parse_puzzle_id(s: str) -> list[int]:
    """Parse a compact puzzle ID (18 hex digits, optional 0x prefix) into 6 piece IDs."""
    hex_str = s[2:] if s.lower().startswith("0x") else s
    if len(hex_str) != 18:
        raise ValueError(f"Puzzle ID must be exactly 18 hex digits, got {len(hex_str)}")
    return [int(hex_str[i:i+3], 16) for i in range(0, 18, 3)]


def is_puzzle_id(s: str) -> bool:
    """Check if a string looks like a compact puzzle ID (18 hex digits, optional 0x prefix)."""
    hex_str = s[2:] if s.lower().startswith("0x") else s
    return len(hex_str) == 18 and all(c in "0123456789abcdefABCDEF" for c in hex_str)


def parse_piece_id(s: str) -> int:
    """Parse a piece ID from a string. Supports decimal, 0x hex, and 0b binary."""
    s = s.strip()
    try:
        return int(s, 0)
    except ValueError:
        raise ValueError(f"Invalid piece ID: {s!r}")


def main():
    parser = argparse.ArgumentParser(
        description="Normalize a 6-piece burr puzzle description."
    )
    parser.add_argument(
        "pieces", nargs="+", metavar="ID",
        help="Piece IDs (decimal, 0x hex, or 0b binary), or a single compact puzzle ID (18 hex digits)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Log each normalization step"
    )
    parser.add_argument(
        "-s", "--strict", action="store_true",
        help="Error if too few cubies removed to be assemblable (need >= 40)"
    )
    args = parser.parse_args()

    try:
        if len(args.pieces) == 1 and is_puzzle_id(args.pieces[0]):
            piece_ids = parse_puzzle_id(args.pieces[0])
        else:
            piece_ids = [parse_piece_id(s) for s in args.pieces]
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        result = normalize(piece_ids, verbose=args.verbose)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.strict:
        removed = total_removed(result)
        if removed < MIN_REMOVED_CUBIES:
            print(
                f"Error: Only {removed} cubies removed across all pieces, "
                f"need at least {MIN_REMOVED_CUBIES} for assembly "
                f"(interior has 32 positions for 72 removable cubies)",
                file=sys.stderr,
            )
            sys.exit(1)
        if args.verbose:
            print(f"  Total cubies removed: {removed} (minimum {MIN_REMOVED_CUBIES})", file=sys.stderr)

    print(" ".join(str(pid) for pid in result))


if __name__ == "__main__":
    main()
