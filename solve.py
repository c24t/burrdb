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
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
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
# Full solution parsing (for animation)
# ==========================================

def parse_assembly(asm_str: str, shape_max_counts: list[int]):
    """Parse BurrTools assembly string into per-slot positions and transforms.

    The assembly string contains 4 values per piece slot: x y z transform_index.
    Slots are ordered by shape index, then by instance within each shape.

    Args:
        asm_str: Space-separated string "x y z trans x y z trans ..."
        shape_max_counts: List of max counts per shape (from <shape max="N"/>)

    Returns:
        (positions, transforms, slot_to_shape):
            positions: list of [x, y, z] per slot (6 total)
            transforms: list of rotation indices per slot
            slot_to_shape: list mapping slot index → shape index
    """
    vals = list(map(int, asm_str.split()))
    positions = []
    transforms = []
    slot_to_shape = []

    idx = 0
    for shape_idx, max_count in enumerate(shape_max_counts):
        for _ in range(max_count):
            x, y, z, trans = vals[idx : idx + 4]
            positions.append([x, y, z])
            transforms.append(trans)
            slot_to_shape.append(shape_idx)
            idx += 4

    return positions, transforms, slot_to_shape


def flatten_separation_tree(
    sep_el, num_pieces: int, last_known: dict, exit_dist: int = 6
) -> list:
    """Recursively flatten a BurrTools separation tree into linear keyframes.

    Walks the nested <separation> XML tree depth-first, collecting piece
    positions at each state.  Exit values (|coord| > 10000) are replaced
    with sensible slide-out positions.

    Args:
        sep_el: <separation> XML element
        num_pieces: Total piece count (always 6 for our burr puzzles)
        last_known: Mutable dict {global_piece_index: [x, y, z]} tracking
                    every piece's most recent position.  Updated in place.
        exit_dist: Distance (in grid units) to slide pieces out on exit.

    Returns:
        List of keyframes.  Each keyframe is a list of [x, y, z] for all
        *num_pieces* pieces (indexed 0 .. num_pieces-1).
    """
    # Map local indices in this node to global piece indices
    pieces_el = sep_el.find("pieces")
    global_indices = list(map(int, pieces_el.text.split()))

    keyframes = []

    for state_el in sep_el.findall("state"):
        dx = list(map(int, state_el.find("dx").text.split()))
        dy = list(map(int, state_el.find("dy").text.split()))
        dz = list(map(int, state_el.find("dz").text.split()))

        for local_idx, global_idx in enumerate(global_indices):
            x, y, z = dx[local_idx], dy[local_idx], dz[local_idx]
            prev = last_known[global_idx]

            # Replace absurd exit values with a slide-out position
            if abs(x) > 10000:
                x = prev[0] + (1 if x > 0 else -1) * exit_dist
            if abs(y) > 10000:
                y = prev[1] + (1 if y > 0 else -1) * exit_dist
            if abs(z) > 10000:
                z = prev[2] + (1 if z > 0 else -1) * exit_dist

            last_known[global_idx] = [x, y, z]

        # Snapshot all pieces' current positions as a keyframe
        keyframes.append([list(last_known[i]) for i in range(num_pieces)])

    # Recurse into child separations (left group first, then removed group)
    for child_type in ("left", "removed"):
        child = sep_el.find(f"separation[@type='{child_type}']")
        if child is not None:
            child_kfs = flatten_separation_tree(
                child, num_pieces, last_known, exit_dist
            )
            # Skip child's first keyframe — it duplicates the current state
            keyframes.extend(child_kfs[1:])

    return keyframes


def compute_level(sep_el) -> str:
    """Compute the disassembly level string from a separation tree.

    Returns a dot-separated string like "5.1.1.1.1" where each number is
    the number of moves at that stage of the disassembly.
    """
    num_moves = len(sep_el.findall("state")) - 1
    sub_levels = []
    for child_type in ("left", "removed"):
        child = sep_el.find(f"separation[@type='{child_type}']")
        if child is not None:
            sub_levels.append(compute_level(child))
    if sub_levels:
        return f"{num_moves}." + ".".join(sub_levels)
    return str(num_moves)


def solve_full(piece_ids: list[int], verbose: bool = False) -> dict:
    """Run the BurrTools solver and return full solution data for animation.

    Unlike solve(), this parses the complete output XML including assembly
    placements, rotation transforms, and the separation tree (flattened
    into linear keyframes).

    Returns a dict:
        pieces: list of 6 input piece IDs
        numAssemblies: int
        numSolutions: int
        solutions: list of dicts, each with:
            pieceIds: list of 6 piece IDs (expanded for duplicates)
            transforms: list of 6 rotation indices
            keyframes: list of keyframes, each [6 × [x, y, z]]
            level: str like "5.1.1.1.1"
    """
    solver = find_solver()
    xmpuzzle_data = generate_xmpuzzle(piece_ids)

    # Recover the shape grouping used by generate_xmpuzzle
    counts = Counter(piece_ids)
    unique_ids = list(dict.fromkeys(piece_ids))
    shape_max_counts = [counts[pid] for pid in unique_ids]

    with tempfile.TemporaryDirectory() as tmpdir:
        puzzle_path = os.path.join(tmpdir, "puzzle.xmpuzzle")
        output_path = puzzle_path + "ttt"

        with open(puzzle_path, "wb") as f:
            f.write(xmpuzzle_data)

        cmd = [str(solver), "-R", "-d", puzzle_path]
        if verbose:
            print(f"  Running: {' '.join(cmd)}", file=sys.stderr)

        read_fd, write_fd = os.pipe()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                stdin=read_fd,
                timeout=300,
            )
        finally:
            os.close(read_fd)
            os.close(write_fd)

        if verbose:
            for line in proc.stdout.strip().split("\n"):
                line = line.strip()
                if line:
                    print(f"  solver: {line}", file=sys.stderr)

        result = {
            "pieces": piece_ids,
            "numAssemblies": 0,
            "numSolutions": 0,
            "solutions": [],
        }

        if not os.path.exists(output_path):
            return result

        tree = ET.parse(output_path)
        xml_root = tree.getroot()

        problem = xml_root.find(".//problem")
        if problem is None:
            return result

        result["numAssemblies"] = int(problem.get("assemblies", "0"))
        result["numSolutions"] = int(problem.get("solutions", "0"))

        for sol_el in xml_root.findall(".//solution"):
            asm_el = sol_el.find("assembly")
            sep_el = sol_el.find("separation")
            if asm_el is None or sep_el is None:
                continue

            positions, transforms, slot_to_shape = parse_assembly(
                asm_el.text, shape_max_counts
            )
            slot_piece_ids = [unique_ids[si] for si in slot_to_shape]

            # Flatten the separation tree into a linear keyframe sequence
            last_known = {i: list(pos) for i, pos in enumerate(positions)}
            keyframes = flatten_separation_tree(sep_el, 6, last_known)

            level = compute_level(sep_el)

            result["solutions"].append(
                {
                    "pieceIds": slot_piece_ids,
                    "transforms": transforms,
                    "keyframes": keyframes,
                    "level": level,
                }
            )

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
