"""Compare parser Markdown with a human-checked reference transcription."""

import argparse
from collections import Counter
from difflib import SequenceMatcher, unified_diff
from pathlib import Path
import re
import sys
import unicodedata

REFERENCE = Path(__file__).with_name("fixtures") / "reference.md"
PAGE = re.compile(r"(?m)^<!-- pdf-page: (\d+) -->[ \t]*$")
MATH = re.compile(r"(?<!\\)\$\$(.+?)\$\$|(?<![\\$])\$([^$\n]+?)\$(?!\$)", re.DOTALL)
TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
TABLE_RULE = re.compile(r"^\s*:?-{3,}:?\s*$")


def read_pages(path):
    """Read Markdown separated by consecutive page markers."""
    parts = PAGE.split(path.read_text(encoding="utf-8"))
    numbers = [int(number) for number in parts[1::2]]
    if parts[0].strip() or not numbers or numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(f"{path}: expected consecutive page markers starting at 1")
    return [page.strip() for page in parts[2::2]]


def normalize_text(text):
    """Normalize Markdown for text scoring without changing its wording."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = MATH.sub(" ", text)
    text = re.sub(r"(?m)^\s*<!--.*?-->\s*$", " ", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*(?:[-*+]\s+|\d+[.)]\s+|>\s*)", "", text)
    text = text.replace("|", " ")
    text = re.sub(r"(?m)^\s*:?-{3,}:?\s*$", " ", text)
    text = re.sub(r"[`*_~]", "", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def edit_distance(left, right):
    """Levenshtein distance, using memory proportional to the shorter input."""
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, left_item in enumerate(left, start=1):
        current = [i]
        for j, right_item in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (left_item != right_item),
            ))
        previous = current
    return previous[-1]


def text_metrics(reference, candidate):
    expected = normalize_text(reference)
    actual = normalize_text(candidate)
    expected_words = expected.split()
    actual_words = actual.split()
    word_errors = edit_distance(expected_words, actual_words)
    char_errors = edit_distance(expected, actual)
    return {
        "expected_words": len(expected_words),
        "actual_words": len(actual_words),
        "word_errors": word_errors,
        "wer": word_errors / len(expected_words) if expected_words else float(bool(actual_words)),
        "expected_chars": len(expected),
        "actual_chars": len(actual),
        "char_errors": char_errors,
        "cer": char_errors / len(expected) if expected else float(bool(actual)),
        "expected_tokens": expected_words,
        "actual_tokens": actual_words,
    }


def content_differences(metrics, limit=6):
    """Show unmatched words; extras are candidates for hallucinations, not proof."""
    # Compare word counts so a reordered phrase is not mislabeled as missing
    # text plus a hallucination; reading order is reported separately.
    expected = Counter(metrics["expected_tokens"])
    actual = Counter(metrics["actual_tokens"])
    return list((expected - actual).elements())[:limit], list((actual - expected).elements())[:limit]


def extract_blocks(page):
    """Return non-empty paragraph-like blocks in their Markdown order."""
    blocks = []
    for block in re.split(r"\n\s*\n", page):
        # Keep a placeholder so equation-only blocks still count in page order.
        block = MATH.sub(" EQUATION ", block)
        normalized = normalize_text(block)
        if normalized:
            blocks.append(normalized)
    return blocks


def reading_order_metrics(reference, candidate):
    expected = extract_blocks(reference)
    actual = extract_blocks(candidate)
    matcher = SequenceMatcher(a=expected, b=actual, autojunk=False)
    matched = sum(size for _, _, size in matcher.get_matching_blocks())
    expected_counter, actual_counter = Counter(expected), Counter(actual)
    shared = sum((expected_counter & actual_counter).values())
    return {
        "expected_blocks": len(expected),
        "actual_blocks": len(actual),
        "matched_in_order": matched,
        "order_score": matched / len(expected) if expected else float(not actual),
        "matched_blocks": shared,
    }


def table_rows(page):
    rows = []
    for line in page.splitlines():
        match = TABLE_ROW.match(line)
        if not match:
            continue
        cells = [normalize_text(cell) for cell in match.group(1).split("|")]
        if cells and all(TABLE_RULE.fullmatch(cell) for cell in line.strip().strip("|").split("|")):
            continue
        rows.append(cells)
    return rows


def table_metrics(reference, candidate):
    expected, actual = table_rows(reference), table_rows(candidate)
    expected_cells = [cell for row in expected for cell in row]
    actual_cells = [cell for row in actual for cell in row]
    expected_counter, actual_counter = Counter(expected_cells), Counter(actual_cells)
    cell_matches = sum((expected_counter & actual_counter).values())
    exact_rows = sum(1 for row in expected if row in actual)
    position_matches = sum(
        1 for row_index, row in enumerate(expected)
        for column_index, cell in enumerate(row)
        if row_index < len(actual) and column_index < len(actual[row_index])
        and actual[row_index][column_index] == cell
    )
    expected_count = sum(map(len, expected))
    return {
        "expected_rows": len(expected),
        "actual_rows": len(actual),
        "expected_cells": expected_count,
        "actual_cells": len(actual_cells),
        "cell_matches": cell_matches,
        "cell_text_score": cell_matches / expected_count if expected_count else None,
        "position_matches": position_matches,
        "cell_position_score": position_matches / expected_count if expected_count else None,
        "exact_rows": exact_rows,
    }


def normalize_equation(expression):
    expression = re.sub(r"\s+", "", expression)
    expression = re.sub(r"\\(?:left|right)(?=[()[\]|])", "", expression)
    expression = re.sub(r"([_^])\{([A-Za-z0-9])\}", r"\1\2", expression)
    return expression


def equations(page):
    return [("display" if display else "inline", normalize_equation(display or inline))
            for display, inline in MATH.findall(page)]


def equation_metrics(reference, candidate):
    expected, actual = Counter(equations(reference)), Counter(equations(candidate))
    matched = sum((expected & actual).values())
    expected_count, actual_count = sum(expected.values()), sum(actual.values())
    precision = matched / actual_count if actual_count else None
    recall = matched / expected_count if expected_count else None
    return {
        "expected": expected_count,
        "actual": actual_count,
        "matched": matched,
        "precision": precision,
        "recall": recall,
        "missing": list((expected - actual).elements()),
        "extra": list((actual - expected).elements()),
    }


def percent(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path, help="Parser output Markdown to evaluate")
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    args = parser.parse_args()
    try:
        expected = read_pages(args.reference)
        actual = read_pages(args.markdown)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print("PDF-to-Markdown evaluation")
    print(f"Reference: {args.reference}")
    print(f"Candidate: {args.markdown}")
    print("Scores compare the candidate with the reference; check flagged differences against the PDF.")
    success = len(expected) == len(actual)
    if not success:
        print(f"\nPage count: expected {len(expected)}, got {len(actual)}.")
    for number, (reference, candidate) in enumerate(zip(expected, actual), start=1):
        text = text_metrics(reference, candidate)
        missing, extra = content_differences(text)
        reading = reading_order_metrics(reference, candidate)
        tables = table_metrics(reference, candidate)
        math = equation_metrics(reference, candidate)
        print(f"\nPage {number}")
        print(f"  Text: WER {percent(text['wer'])} ({text['word_errors']} word edits; "
              f"{text['expected_words']} reference words), CER {percent(text['cer'])} "
              f"({text['char_errors']} character edits; {text['expected_chars']} reference characters).")
        print(f"  Reading order: {percent(reading['order_score'])} "
              f"({reading['matched_in_order']}/{reading['expected_blocks']} reference blocks matched in order; "
              f"{reading['matched_blocks']} blocks present regardless of order).")
        print(f"  Tables: cell text {percent(tables['cell_text_score'])}; "
              f"cell position {percent(tables['cell_position_score'])}; "
              f"{tables['exact_rows']}/{tables['expected_rows']} exact reference rows; "
              f"{tables['actual_rows']} rows found.")
        print(f"  Equations: precision {percent(math['precision'])}, recall {percent(math['recall'])} "
              f"({math['matched']}/{math['expected']} reference equations matched; "
              f"{math['actual']} found).")
        if missing:
            print("  Missing from candidate (review against PDF): " + "; ".join(missing))
        if extra:
            print("  Extra vs reference, possibly hallucinated (verify against PDF): " + "; ".join(extra))
        for label, items in (("Missing equations", math["missing"]), ("Extra equations", math["extra"])):
            if items:
                print(f"  {label}: " + "; ".join(f"{kind}: {expression}" for kind, expression in items))
        # Preserve the original structured-layout check and detailed line diff.
        layout_ok = layout_lines(reference) == layout_lines(candidate)
        print("  Legacy layout/order check: " + ("pass" if layout_ok else "review"))
        expected_lines = [line.strip() for line in reference.splitlines() if line.strip()]
        actual_lines = [line.strip() for line in candidate.splitlines() if line.strip()]
        diff = list(unified_diff(expected_lines, actual_lines, "reference", "output", lineterm=""))
        if diff:
            print("  Detailed transcription diff:")
            print("\n".join(diff))
        else:
            print("  Transcription: exact line match.")
        success &= layout_ok and math["missing"] == [] and math["extra"] == [] and text["wer"] == 0 and not diff
    return 0 if success else 1


def layout_lines(page):
    """Legacy heading/list/callout/table sequence check retained for compatibility."""
    lines = []
    for line in page.splitlines():
        if re.match(r"^#{1,6} ", line):
            lines.append(("heading", normalize_text(line)))
        elif re.match(r"^[-*+] ", line):
            lines.append(("list item", normalize_text(line)))
        elif re.match(r"^>\s*", line):
            lines.append(("callout", normalize_text(line)))
        elif TABLE_ROW.match(line):
            cells = [normalize_text(cell) for cell in line.strip().strip("|").split("|")]
            if not all(TABLE_RULE.fullmatch(cell) for cell in line.strip().strip("|").split("|")):
                lines.append(("table row", " | ".join(cells)))
    return lines


if __name__ == "__main__":
    sys.exit(main())
