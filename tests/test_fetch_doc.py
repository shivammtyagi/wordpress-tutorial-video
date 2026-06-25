import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "doc.html")


def test_fetch_doc_cleans_html(tmp_path):
    subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "fetch_doc.py"),
         "--run-dir", str(tmp_path), "--html-file", FIXTURE],
        check=True,
    )
    doc = (tmp_path / "doc.md").read_text()
    # keeps headings + body
    assert "# Setting up XML Sitemaps" in doc
    assert "## Step 1: Open the Sitemaps screen" in doc
    assert "Toggle Enable Sitemap on." in doc
    # strips chrome + scripts/styles
    assert "tracking pixel" not in doc
    assert "font-family" not in doc
    assert "Copyright footer chrome" not in doc
    assert "Site Header Chrome" not in doc
    # collects images
    assert "## Images" in doc
    assert "sitemap-toggle.png" in doc
    assert "The enable sitemap toggle" in doc
