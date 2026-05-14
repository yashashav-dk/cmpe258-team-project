"""Build report HTML + PDF from docs/report.md.

Steps:
  1. Read docs/report.md.
  2. Render to HTML with extensions (tables, fenced_code, toc, codehilite).
  3. Wrap in a print-friendly stylesheet.
  4. Write docs/report.html.
  5. Invoke headless Chrome to print docs/report.pdf.

Run:
    python3 scripts/build_report.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import markdown


REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
MD_PATH = DOCS_DIR / "report.md"
HTML_PATH = DOCS_DIR / "report.html"
PDF_PATH = DOCS_DIR / "report.pdf"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
]


CSS = r"""
:root {
  --fg: #1a1a1a;
  --muted: #555;
  --rule: #d8d8d8;
  --accent: #1f3a93;
  --code-bg: #f6f6f7;
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
  font-family: "Charter", "Georgia", "Times New Roman", serif;
  font-size: 10.5pt;
  line-height: 1.45;
  color: var(--fg);
}

body { padding: 0 0.6in; }

h1, h2, h3, h4 {
  font-family: "Helvetica Neue", "Helvetica", "Arial", sans-serif;
  color: var(--accent);
  page-break-after: avoid;
}

h1 {
  font-size: 22pt;
  margin: 0.4em 0 0.3em;
  border-bottom: 2px solid var(--accent);
  padding-bottom: 0.2em;
}

h2 {
  font-size: 15pt;
  margin: 1.1em 0 0.35em;
  border-bottom: 1px solid var(--rule);
  padding-bottom: 0.15em;
}

h3 {
  font-size: 12pt;
  margin: 0.9em 0 0.25em;
}

h4 {
  font-size: 10.5pt;
  margin: 0.7em 0 0.2em;
  font-style: italic;
}

p { margin: 0 0 0.55em; text-align: justify; }

ul, ol { margin: 0.2em 0 0.6em 1.3em; padding: 0; }
li { margin-bottom: 0.18em; }

table {
  width: 100%;
  border-collapse: collapse;
  margin: 0.6em 0 0.9em;
  font-size: 9.5pt;
  page-break-inside: avoid;
}

th, td {
  border: 1px solid var(--rule);
  padding: 4px 8px;
  text-align: left;
  vertical-align: top;
}

th {
  background: #eef2fb;
  color: var(--accent);
  font-weight: 600;
}

tr:nth-child(even) td { background: #fafbfd; }

code {
  font-family: "JetBrains Mono", "Menlo", "Consolas", monospace;
  font-size: 9pt;
  background: var(--code-bg);
  padding: 1px 4px;
  border-radius: 3px;
}

pre {
  background: var(--code-bg);
  border: 1px solid var(--rule);
  border-left: 3px solid var(--accent);
  padding: 8px 10px;
  border-radius: 3px;
  overflow: hidden;
  font-size: 8.8pt;
  line-height: 1.35;
  white-space: pre-wrap;
  word-break: break-word;
  page-break-inside: avoid;
}

pre code { background: transparent; padding: 0; font-size: inherit; }

blockquote {
  margin: 0.5em 0;
  padding: 0.2em 0.8em;
  border-left: 3px solid var(--accent);
  background: #f7f9fc;
  color: var(--muted);
  font-size: 9.8pt;
}

hr {
  border: none;
  border-top: 1px solid var(--rule);
  margin: 1.0em 0;
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

@page {
  size: Letter;
  margin: 0.75in 0.6in 0.85in 0.6in;
  @bottom-center {
    content: "CMPE 258 Final Report — Bug-Squashing Agent — Page " counter(page) " of " counter(pages);
    font-family: "Helvetica Neue", sans-serif;
    font-size: 8.5pt;
    color: #777;
  }
}

/* Avoid orphan headings */
h1, h2, h3 { page-break-after: avoid; }
table, pre, blockquote { page-break-inside: avoid; }
"""


def render_html(md_text: str) -> str:
    md_engine = markdown.Markdown(
        extensions=[
            "extra",
            "tables",
            "fenced_code",
            "sane_lists",
            "toc",
            "codehilite",
        ],
        extension_configs={
            "codehilite": {"guess_lang": False, "noclasses": True, "pygments_style": "friendly"},
            "toc": {"permalink": False},
        },
        output_format="html5",
    )
    body = md_engine.convert(md_text)
    return f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\" />
<title>CMPE 258 Final Report — Bug-Squashing Agent</title>
<style>{CSS}</style>
</head>
<body>
{body}
</body>
</html>
"""


def write_pdf(html_path: Path, pdf_path: Path) -> None:
    chrome = next((Path(c) for c in CHROME_CANDIDATES if c and Path(c).exists()), None)
    if chrome is None:
        print("ERROR: no Chrome / Chromium found; cannot render PDF", file=sys.stderr)
        sys.exit(1)
    cmd = [
        str(chrome),
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--no-sandbox",
        f"--print-to-pdf={pdf_path}",
        f"file://{html_path.resolve()}",
    ]
    print("Running:", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stdout)
        print(res.stderr, file=sys.stderr)
        sys.exit(res.returncode)


def main() -> None:
    md_text = MD_PATH.read_text(encoding="utf-8")
    html = render_html(md_text)
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {HTML_PATH} ({len(html):,} bytes)")
    write_pdf(HTML_PATH, PDF_PATH)
    print(f"Wrote {PDF_PATH} ({PDF_PATH.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
