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
    order_precision, order_recall, order_f1 = precision_recall_f1(
        matched, len(actual), len(expected)
    )
    content_precision, content_recall, content_f1 = precision_recall_f1(
        shared, len(actual), len(expected)
    )
    return {
        "expected_blocks": len(expected),
        "actual_blocks": len(actual),
        "matched_in_order": matched,
        "matched_blocks": shared,
        "order_precision": order_precision,
        "order_recall": order_recall,
        "order_f1": order_f1,
        "content_precision": content_precision,
        "content_recall": content_recall,
        "content_f1": content_f1,
    }


def precision_recall_f1(matched, predicted, expected):
    precision = matched / predicted if predicted else (0.0 if expected else None)
    recall = matched / expected if expected else (0.0 if predicted else None)
    if precision is None and recall is None:
        f1 = None
    elif (precision or 0) + (recall or 0) == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def table_grids(page):
    """Read consecutive pipe-formatted Markdown tables as cell grids."""
    tables = []
    rows = []
    for line in page.splitlines():
        match = TABLE_ROW.match(line)
        if not match:
            if rows:
                tables.append(rows)
                rows = []
            continue
        raw_cells = line.strip().strip("|").split("|")
        if raw_cells and all(TABLE_RULE.fullmatch(cell.strip()) for cell in raw_cells):
            continue
        rows.append([normalize_text(cell) for cell in match.group(1).split("|")])
    if rows:
        tables.append(rows)
    return tables


def table_metrics(reference, candidate):
    expected, actual = table_grids(reference), table_grids(candidate)
    expected_cells = [cell for table in expected for row in table for cell in row]
    actual_cells = [cell for table in actual for row in table for cell in row]
    expected_counter, actual_counter = Counter(expected_cells), Counter(actual_cells)
    cell_matches = sum((expected_counter & actual_counter).values())
    position_matches = 0
    for table_index, table in enumerate(expected):
        if table_index >= len(actual):
            continue
        for row_index, row in enumerate(table):
            if row_index >= len(actual[table_index]):
                continue
            candidate_row = actual[table_index][row_index]
            position_matches += sum(
                1 for column_index, cell in enumerate(row)
                if column_index < len(candidate_row) and candidate_row[column_index] == cell
            )
    table_matcher = SequenceMatcher(a=expected, b=actual, autojunk=False)
    exact_tables = sum(size for _, _, size in table_matcher.get_matching_blocks())
    cell_precision, cell_recall, cell_f1 = precision_recall_f1(
        cell_matches, len(actual_cells), len(expected_cells)
    )
    position_precision, position_recall, position_f1 = precision_recall_f1(
        position_matches, len(actual_cells), len(expected_cells)
    )
    table_precision, table_recall, table_f1 = precision_recall_f1(
        exact_tables, len(actual), len(expected)
    )
    return {
        "expected_tables": len(expected),
        "actual_tables": len(actual),
        "expected_rows": sum(map(len, expected)),
        "actual_rows": sum(map(len, actual)),
        "expected_cells": len(expected_cells),
        "actual_cells": len(actual_cells),
        "cell_matches": cell_matches,
        "cell_precision": cell_precision,
        "cell_recall": cell_recall,
        "cell_f1": cell_f1,
        "position_matches": position_matches,
        "position_precision": position_precision,
        "position_recall": position_recall,
        "position_f1": position_f1,
        "exact_tables": exact_tables,
        "table_precision": table_precision,
        "table_recall": table_recall,
        "table_f1": table_f1,
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
    expected_sequence, actual_sequence = equations(reference), equations(candidate)
    expected, actual = Counter(expected_sequence), Counter(actual_sequence)
    matched = sum((expected & actual).values())
    expected_count, actual_count = len(expected_sequence), len(actual_sequence)
    precision, recall, f1 = precision_recall_f1(matched, actual_count, expected_count)
    matcher = SequenceMatcher(a=expected_sequence, b=actual_sequence, autojunk=False)
    ordered_matches = sum(size for _, _, size in matcher.get_matching_blocks())
    order_precision, order_recall, order_f1 = precision_recall_f1(
        ordered_matches, actual_count, expected_count
    )
    return {
        "expected": expected_count,
        "actual": actual_count,
        "matched": matched,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "ordered_matches": ordered_matches,
        "order_precision": order_precision,
        "order_recall": order_recall,
        "order_f1": order_f1,
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
    total = {
        "expected_words": 0, "actual_words": 0, "word_errors": 0,
        "expected_chars": 0, "actual_chars": 0, "char_errors": 0,
    }
    if not success:
        print(f"\nPage count: expected {len(expected)}, got {len(actual)}.")
    for index in range(max(len(expected), len(actual))):
        reference = expected[index] if index < len(expected) else ""
        candidate = actual[index] if index < len(actual) else ""
        number = index + 1
        text = text_metrics(reference, candidate)
        for key in total:
            total[key] += text[key]
        missing, extra = content_differences(text)
        reading = reading_order_metrics(reference, candidate)
        tables = table_metrics(reference, candidate)
        math = equation_metrics(reference, candidate)
        print(f"\nPage {number}")
        print(f"  Text: WER {percent(text['wer'])} ({text['word_errors']} word edits; "
              f"{text['expected_words']} reference words), CER {percent(text['cer'])} "
              f"({text['char_errors']} character edits; {text['expected_chars']} reference characters).")
        print(f"  Reading order: ordered F1 {percent(reading['order_f1'])} "
              f"(precision {percent(reading['order_precision'])}, "
              f"recall {percent(reading['order_recall'])}; "
              f"{reading['matched_in_order']} ordered matches from "
              f"{reading['expected_blocks']} reference / {reading['actual_blocks']} output blocks); "
              f"block-content F1 {percent(reading['content_f1'])} regardless of order.")
        print(f"  Tables: cell F1 {percent(tables['cell_f1'])} "
              f"(precision {percent(tables['cell_precision'])}, recall {percent(tables['cell_recall'])}); "
              f"position F1 {percent(tables['position_f1'])}; "
              f"grid F1 {percent(tables['table_f1'])} "
              f"({tables['exact_tables']}/{tables['expected_tables']} expected, "
              f"{tables['actual_tables']} output).")
        print(f"  Equations: F1 {percent(math['f1'])} "
              f"(precision {percent(math['precision'])}, recall {percent(math['recall'])}); "
              f"order F1 {percent(math['order_f1'])}; "
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
    total_wer = (total["word_errors"] / total["expected_words"]
                 if total["expected_words"] else float(bool(total["actual_words"])))
    total_cer = (total["char_errors"] / total["expected_chars"]
                 if total["expected_chars"] else float(bool(total["actual_chars"])))
    print(f"\nDocument totals: WER {percent(total_wer)} "
          f"({total['word_errors']} edits / {total['expected_words']} reference words), "
          f"CER {percent(total_cer)} "
          f"({total['char_errors']} edits / {total['expected_chars']} reference characters).")
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
