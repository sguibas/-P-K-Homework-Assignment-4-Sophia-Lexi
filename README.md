# Option C PDF parser

A Python prototype that renders a PDF to page images with pypdfium2. It sends one
image per request to the OpenAI Responses API and combines the returned Markdown
with numbered HTML comments. The prompt asks for headings, lists, tables,
callouts, and LaTeX math in reading order.

## Run in VS Code on macOS

Open this folder in VS Code. Open the integrated terminal and run:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p runs
```

PDF rendering is provided by pypdfium2, so no separate Poppler installation is
needed. Open the [Codex extension](https://learn.chatgpt.com/docs/codex/ide)
sidebar to work on the code. The parser needs an OpenAI API key with model
access for its own requests. Enter the key in the terminal with input hidden:

```sh
printf 'OpenAI API key: '
read -rs OPENAI_API_KEY
printf '\n'
export OPENAI_API_KEY
python pdf_to_markdown.py fixtures/challenge.pdf runs/challenge.md
python evaluate.py runs/challenge.md
unset OPENAI_API_KEY
```

The model calls use API credits and send rendered pages to OpenAI. Use the
included synthetic PDF for the first run. Only use work documents when you have
permission to send them to the API.

The terminal shows the model, render DPI, per-page latency and token counts,
total rendering/API/run time, and an estimated API token cost. The estimate uses
the standard GPT-5.4 mini rates checked on November 10, 2026; actual billing can
differ if rates or account settings change. For a `--model` override, the parser
reports token usage but asks you to check that model's current rates. To compare
settings, rerun with `--dpi 240` or `--model MODEL_ID` and save the results to a
different Markdown file. Use `--max-pages 1` for a one-page trial.

## Check accuracy

- **Page 1** uses two staggered columns, a bulleted list, a table, a shaded
  callout, and a full-width handoff section. Check that the left column stays
  together and that the values stay attached to their sample IDs.
- **Page 2** contains inline math, a stacked fraction, a square root, scripts,
  and equation numbers. Check that the grouping and the labels survive.
- Compare the PDF, the output, and `fixtures/reference.md`. The evaluator
  reports normalized WER/CER, reading-order precision/recall/F1, table cell and
  grid scores, equation precision/recall/F1, and a line diff. Text normalization
  ignores case, punctuation, Markdown markers, and dollar-delimited math;
  equations are compared separately by normalized LaTeX text, not mathematical
  equivalence. Extra words mean extra relative to the reference, so verify them
  against the PDF before calling them hallucinations. It exits with status 1 for
  a detected text or structure difference, including harmless reformatting.

To check the evaluator's formatting and report output without an API key, run
`python evaluate.py fixtures/reference.md`. This compares the reference with
itself; it does not measure model extraction accuracy. For a team comparison,
use the same PDFs and human-checked references for every approach, and record
the exact model, DPI, parser version, error examples, timing, tokens, and cost.
This fixture is a two-page smoke test.
