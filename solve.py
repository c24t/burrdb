#!/usr/bin/env python3
"""Check whether a 6-piece burr puzzle is solvable using BurrTools.

Generates an .xmpuzzle file from 6 piece IDs, invokes the burrTxt2
solver, and reports whether the puzzle has valid assemblies that can
be disassembled.

Usage:
    python solve.py 103 205 3175 3322 3324 3326
    python solve.py 0x0670CDC67CFACFCCFE
    python solve.py -v 1 205 222 3328 3328 3328
"""

import argparse
import gzip
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from normalize import (
    BIT_MAP,
    is_connected,
    parse_piece_id,
    parse_puzzle_id,
    is_puzzle_id,
)

# Path to burrTxt2 solver binary.
# In a worktree, __file__ may not be at the repo root, so we also check
# the git toplevel and common locations.
def _find_solver_candidates():
    here = Path(__file__).resolve().parent
    candidates = [
        here / "src" / "burr-tools" / "build" / "burrTxt2",
    ]
    # Walk up to find the repo root (has a src/burr-tools dir)
    for parent in here.parents:
        candidate = parent / "src" / "burr-tools" / "build" / "burrTxt2"
        if candidate.exists():
            candidates.insert(0, candidate)
            break
    return candidates

# ==========================================
# Voxel string generation
# ==========================================

def piece_id_to_voxel_string(piece_id: int) -> str:
    """Convert a piece ID (1-4096) to a 24-char BurrTools voxel string.

    The string encodes a 6x2x2 grid where index = x + 6*y + 12*z.
    '#' = solid, '_' = empty (removed cubie).
    """
    bitmap = piece_id - 1
    bit_map_set = set(BIT_MAP)

    chars = []
    for z in range(2):
        for y in range(2):
            for x in range(6):
                pos = (x, y, z)
                if pos in bit_map_set:
                    bit_idx = BIT_MAP.index(pos)
                    is_removed = (bitmap >> bit_idx) & 1
                    chars.append("_" if is_removed else "#")
                else:
                    # Fixed position — always solid
                    chars.append("#")
    return "".join(chars)


def build_target_voxel_string() -> str:
    """Build the 6x6x6 target shape for a general 6-piece burr.

    Exterior positions (belonging to exactly 1 piece slot) are '#'.
    Interior positions (shared by 2+ slots) are '+' (variable).
    """
    # The 6 piece slots in the assembled burr
    slots = [
        dict(x=range(6), y=range(2, 4), z=range(1, 3)),  # X slot 0
        dict(x=range(6), y=range(2, 4), z=range(3, 5)),  # X slot 1
        dict(x=range(1, 3), y=range(6), z=range(2, 4)),  # Y slot 2
        dict(x=range(3, 5), y=range(6), z=range(2, 4)),  # Y slot 3
        dict(x=range(2, 4), y=range(1, 3), z=range(6)),  # Z slot 4
        dict(x=range(2, 4), y=range(3, 5), z=range(6)),  # Z slot 5
    ]

    chars = []
    for z in range(6):
        for y in range(6):
            for x in range(6):
                count = sum(
                    1
                    for s in slots
                    if x in s["x"] and y in s["y"] and z in s["z"]
                )
                if count == 0:
                    chars.append("_")
                elif count == 1:
                    chars.append("#")
                else:
                    chars.append("+")
    return "".join(chars)


# ==========================================
# xmpuzzle XML generation
# ==========================================

def generate_xmpuzzle(piece_ids: list[int]) -> bytes:
    """Generate a gzipped .xmpuzzle file for a 6-piece burr puzzle.

    Groups identical pieces and uses min="0" max=count for each group.
    This forces BurrTools to use assembler_1 (assembler_0 crashes on our
    puzzles because it requires min==max==1 for all shapes).

    The puzzle defines:
    - N unique piece shapes (indices 0..N-1)
    - 1 target shape (index N) — the general burr cross with variable interior
    - 1 problem: assemble pieces into the target
    """
    target = build_target_voxel_string()

    # Group identical pieces: maintain a stable order (first occurrence)
    counts = Counter(piece_ids)
    unique_ids = list(dict.fromkeys(piece_ids))  # preserves first-occurrence order

    lines = [
        '<?xml version="1.0"?>',
        '<puzzle version="2">',
        '  <gridType type="0"/>',
        "  <colors/>",
        "  <shapes>",
    ]

    # Add one shape per unique piece
    for pid in unique_ids:
        voxel = piece_id_to_voxel_string(pid)
        lines.append(f'    <voxel x="6" y="2" z="2" type="0">{voxel}</voxel>')

    # Add target shape (its index = len(unique_ids))
    target_idx = len(unique_ids)
    lines.append(f'    <voxel x="6" y="6" z="6" type="0">{target}</voxel>')

    lines.append("  </shapes>")

    # Define the problem: use min="0" max=count for each group.
    # min=0 ensures min != max, which prevents assembler_0 from being
    # selected (it only handles min==max==1). The target's 72 exterior
    # positions guarantee the solver must actually use all 6 pieces.
    lines.append("  <problems>")
    lines.append("    <problem>")
    lines.append("      <shapes>")
    for i, pid in enumerate(unique_ids):
        c = counts[pid]
        lines.append(f'        <shape id="{i}" min="0" max="{c}"/>')
    lines.append("      </shapes>")
    lines.append(f'      <result id="{target_idx}"/>')
    lines.append("      <bitmap/>")
    lines.append("    </problem>")
    lines.append("  </problems>")

    lines.append("</puzzle>")

    xml_content = "\n".join(lines) + "\n"
    return gzip.compress(xml_content.encode("utf-8"))


# ==========================================
# Solver invocation
# ==========================================

def find_solver() -> Path:
    """Find the burrTxt2 binary."""
    for candidate in _find_solver_candidates():
        if candidate.exists():
            return candidate
    # Also check PATH
    result = subprocess.run(["which", "burrTxt2"], capture_output=True, text=True)
    if result.returncode == 0:
        return Path(result.stdout.strip())
    raise FileNotFoundError(
        "burrTxt2 not found. "
        "Build it with: cd src/burr-tools && meson setup build && ninja -C build burrTxt2"
    )


def solve(piece_ids: list[int], verbose: bool = False) -> dict:
    """Run the BurrTools solver on a 6-piece burr puzzle.

    Returns a dict with:
        solvable: bool — whether any disassemblable solution exists
        assemblies: int — number of assemblies found
        solutions: int — number of solutions (assemblies that disassemble)
    """
    solver = find_solver()
    xmpuzzle_data = generate_xmpuzzle(piece_ids)

    with tempfile.TemporaryDirectory() as tmpdir:
        puzzle_path = os.path.join(tmpdir, "puzzle.xmpuzzle")
        output_path = puzzle_path + "ttt"

        with open(puzzle_path, "wb") as f:
            f.write(xmpuzzle_data)

        # Run solver: -d = check disassembly, -R = restart (ignore saved state)
        cmd = [str(solver), "-R", "-d", puzzle_path]
        if verbose:
            print(f"  Running: {' '.join(cmd)}", file=sys.stderr)

        # burrTxt2 polls stdin with select() to check for abort signals.
        # If stdin is EOF (closed pipe or /dev/null), select() returns
        # "readable" immediately and the solver aborts.  We need a pipe
        # that stays open but never delivers data, so select() times out.
        read_fd, write_fd = os.pipe()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                stdin=read_fd,
                timeout=300,  # 5 minute timeout
            )
        finally:
            os.close(read_fd)
            os.close(write_fd)

        if verbose:
            for line in proc.stdout.strip().split("\n"):
                line = line.strip()
                if line:
                    print(f"  solver: {line}", file=sys.stderr)

        # Parse the output XML for solution counts
        result = {"solvable": False, "assemblies": 0, "solutions": 0}

        if os.path.exists(output_path):
            with open(output_path, "r") as f:
                output_xml = f.read()

            # Look for problem state attributes
            m = re.search(
                r'<problem\s+state="(\d+)"'
                r'(?:\s+assemblies="(\d+)")?'
                r'(?:\s+solutions="(\d+)")?',
                output_xml,
            )
            if m:
                assemblies = int(m.group(2)) if m.group(2) else 0
                solutions = int(m.group(3)) if m.group(3) else 0
                result["assemblies"] = assemblies
                result["solutions"] = solutions
                result["solvable"] = solutions > 0

    return result


# ==========================================
# CLI
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description="Check whether a 6-piece burr puzzle is solvable."
    )
    parser.add_argument(
        "pieces",
        nargs="+",
        metavar="ID",
        help="Piece IDs (decimal, 0x hex, or 0b binary), or a single compact puzzle ID (18 hex digits)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show solver progress"
    )
    args = parser.parse_args()

    # Parse piece IDs (same logic as normalize.py)
    try:
        if len(args.pieces) == 1 and is_puzzle_id(args.pieces[0]):
            piece_ids = parse_puzzle_id(args.pieces[0])
        else:
            piece_ids = [parse_piece_id(s) for s in args.pieces]
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if len(piece_ids) != 6:
        print(f"Error: Expected 6 pieces, got {len(piece_ids)}", file=sys.stderr)
        sys.exit(1)

    for pid in piece_ids:
        if not (1 <= pid <= 4096):
            print(
                f"Error: Piece ID {pid} out of range (must be 1-4096)", file=sys.stderr
            )
            sys.exit(1)
        if not is_connected(pid):
            print(f"Error: Piece {pid} is disconnected", file=sys.stderr)
            sys.exit(1)

    try:
        result = solve(piece_ids, verbose=args.verbose)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Error: Solver timed out after 5 minutes", file=sys.stderr)
        sys.exit(1)

    if result["solvable"]:
        print(
            f"SOLVABLE: {result['solutions']} solution(s) "
            f"from {result['assemblies']} assembl{'y' if result['assemblies'] == 1 else 'ies'}"
        )
    else:
        if result["assemblies"] > 0:
            print(
                f"UNSOLVABLE: {result['assemblies']} assembl{'y' if result['assemblies'] == 1 else 'ies'} "
                f"found but none can be disassembled"
            )
        else:
            print("UNSOLVABLE: no valid assemblies found")

    sys.exit(0 if result["solvable"] else 1)


if __name__ == "__main__":
    main()
