# Option C PDF parser

A Python prototype that renders a PDF to page images with Poppler. It sends one
image per request to the OpenAI Responses API and combines the returned Markdown
with numbered HTML comments. The prompt asks for headings, lists, tables,
callouts, and LaTeX math in reading order.

## Run in VS Code on macOS

Open this folder in VS Code. Open the integrated terminal and run:

```sh
brew install poppler
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p runs
```

Skip the Homebrew command if `pdftoppm` is already on your PATH. Open the
[Codex extension](https://learn.chatgpt.com/docs/codex/ide) sidebar to work on
the code. The parser needs an OpenAI API key with model access for its own
requests. Enter the key in the terminal with input hidden:

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

The terminal shows model, render DPI, latency, and token counts. To compare
settings, rerun with `--dpi 240` or `--model MODEL_ID` and save the results to a
different Markdown file. Use `--max-pages 1` for a one-page trial.

## Check accuracy

- **Page 1** uses two staggered columns, a bulleted list, a table, a shaded
  callout, and a full-width handoff section. Check that the left column stays
  together and that the values stay attached to their sample IDs.
- **Page 2** contains inline math, a stacked fraction, a square root, scripts,
  and equation numbers. Check that the grouping and the labels survive.
- Compare the PDF, the output, and `fixtures/reference.md`. The evaluator
  checks heading/list/table/callout order and counts matching and unexpected
  LaTeX expressions per page. Its diff helps spot omissions and invented text.
  It exits with status 1 for any diff, including harmless reformatting. Judge
  math equivalence and actual errors by eye before recording results.

To verify the evaluator itself without an API key, run
`python evaluate.py fixtures/reference.md`. For a team comparison, record the
model and DPI alongside specific missed, changed, or invented content, latency,
and token counts. This fixture is a two-page smoke test.
