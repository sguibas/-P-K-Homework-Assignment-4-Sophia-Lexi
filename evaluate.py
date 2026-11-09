"""Compare a parser run with the hand-transcribed fixture."""

import argparse
from collections import Counter
from difflib import unified_diff
from pathlib import Path
import re
import sys

REFERENCE = Path(__file__).with_name("fixtures") / "reference.md"
PAGE = re.compile(r"(?m)^<!-- pdf-page: (\d+) -->[ \t]*$")
MATH = re.compile(r"(?<!\\)\$\$(.+?)\$\$|(?<![\\$])\$([^$\n]+?)\$(?!\$)", re.DOTALL)
LAYOUT = re.compile(r"(?m)^(#{1,6} |[-*+] |>[ \t]*|\|)(.+)$")


def read_pages(path):
    parts = PAGE.split(path.read_text(encoding="utf-8"))
    numbers = [int(number) for number in parts[1::2]]
    if parts[0].strip() or not numbers or numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(f"{path}: expected consecutive page markers starting at 1")
    return [page.strip() for page in parts[2::2]]


def layout_lines(page):
    lines = []
    for prefix, text in LAYOUT.findall(page):
        if prefix == "|":
            cells = [cell.strip() for cell in text.rstrip().removesuffix("|").split("|")]
            if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                continue
            lines.append(("table row", " | ".join(cells)))
        elif prefix.startswith("#"):
            lines.append(("heading", text.strip()))
        elif prefix.startswith(">"):
            lines.append(("callout", text.strip()))
        else:
            lines.append(("list item", text.strip()))
    return lines


def equations(page):
    matches = []
    for display, inline in MATH.findall(page):
        expression = re.sub(r"\s+", "", display or inline)
        expression = re.sub(r"\\(?:left|right)(?=[()[\]|])", "", expression)
        expression = re.sub(r"([_^])\{([A-Za-z0-9])\}", r"\1\2", expression)
        matches.append(("display" if display else "inline", expression))
    return matches


def compare_math(expected, actual):
    expected_counts = Counter(expected)
    actual_counts = Counter(actual)
    matched = sum((expected_counts & actual_counts).values())
    print(f"  Math: {matched}/{len(expected)} expected matched"
          f" ({len(actual)} found).")
    for kind, expression in (expected_counts - actual_counts).elements():
        print(f"    Missing {kind}: {expression}")
    for kind, expression in (actual_counts - expected_counts).elements():
        print(f"    Unexpected {kind}: {expression}")
    return expected_counts == actual_counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    args = parser.parse_args()
    try:
        expected = read_pages(args.reference)
        actual = read_pages(args.markdown)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    success = len(expected) == len(actual)
    if not success:
        print(f"Page count: expected {len(expected)}, got {len(actual)}.")
    for number, (reference, candidate) in enumerate(zip(expected, actual), start=1):
        print(f"Page {number}")
        layout_ok = layout_lines(reference) == layout_lines(candidate)
        print("  Layout and reading order: " + ("pass" if layout_ok else "review"))
        math_ok = compare_math(equations(reference), equations(candidate))
        # The diff also surfaces missing or extra prose outside the structural checks.
        expected_lines = [line.strip() for line in reference.splitlines() if line.strip()]
        actual_lines = [line.strip() for line in candidate.splitlines() if line.strip()]
        diff = list(unified_diff(expected_lines, actual_lines, "reference", "output",
                                 lineterm=""))
        if diff:
            print("  Transcription differences (including formatting):")
            print("\n".join(diff))
        else:
            print("  Transcription: exact line match.")
        success &= layout_ok and math_ok and not diff
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
