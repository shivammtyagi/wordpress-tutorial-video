import base64
import os
import struct
import subprocess
import sys
import zlib

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "image_card.py")


def _tiny_png(path):
    # 1x1 blue PNG, stdlib-only
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x5a\xe0")
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    open(path, "wb").write(png)


def test_writes_full_bleed_card(tmp_path):
    img = tmp_path / "card.png"
    _tiny_png(str(img))
    subprocess.run([sys.executable, SCRIPT, "--run-dir", str(tmp_path),
                    "--image", str(img), "--card", "intro"], check=True)
    html = (tmp_path / "assets" / "card_intro.html").read_text()
    assert "data:image/png;base64," in html
    assert base64.b64encode(open(img, "rb").read()).decode() in html
    assert "object-fit: cover" in html


def test_rejects_unknown_type(tmp_path):
    bad = tmp_path / "x.txt"
    bad.write_text("nope")
    r = subprocess.run([sys.executable, SCRIPT, "--run-dir", str(tmp_path),
                        "--image", str(bad), "--card", "outro"], capture_output=True)
    assert r.returncode != 0
