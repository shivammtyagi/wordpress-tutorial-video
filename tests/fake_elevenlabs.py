"""A tiny offline stand-in for the ElevenLabs API (tests only, never bills).

Speech: every character takes CHAR_S seconds; letters/digits are a tone, the
rest is silence, so the audio really has pauses where the text has spaces and
punctuation. The alignment is exact. Behaviour switches by voice id:
  VOICE_OK      normal
  VOICE_401     every speech call -> 401 invalid_api_key
  VOICE_QUOTA   every speech call -> 401 quota_exceeded
  VOICE_NOALIGN speech without an alignment
The first pcm_48000 request is refused when the server is started with
refuse_48k=True (plans below Pro cannot use every format).
"""
import base64
import json
import math
import threading
from array import array
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

CHAR_S = 0.05
VOICE_OK = "A" * 20
VOICE_401 = "B" * 20
VOICE_QUOTA = "C" * 20
VOICE_NOALIGN = "D" * 20
VOICES = [
    {"voice_id": VOICE_OK, "name": "Eric - Smooth, Trustworthy", "category": "premade",
     "labels": {"gender": "male", "accent": "american"}},
    {"voice_id": "E" * 20, "name": "Sarah - Mature, Reassuring", "category": "premade", "labels": {}},
]


def speech(text, sr):
    n = int(CHAR_S * sr)
    out = array("h")
    for i, ch in enumerate(text):
        if ch.isalnum():
            out.extend(int(7000 * math.sin(2 * math.pi * 200 * k / sr)) for k in range(n))
        else:
            out.extend([0] * n)
    starts = [round(i * CHAR_S, 4) for i in range(len(text))]
    return out.tobytes(), {"characters": list(text), "character_start_times_seconds": starts,
                           "character_end_times_seconds": [round(s + CHAR_S, 4) for s in starts]}


class Fake:
    def __init__(self, refuse_48k=False):
        self.requests = []          # speech request bodies, in order
        self.formats = []
        self.keys = []
        self.refuse_48k = refuse_48k
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj, headers=None):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.keys.append(self.headers.get("xi-api-key"))
                if urlparse(self.path).path == "/v2/voices":
                    return self._send(200, {"voices": VOICES, "has_more": False})
                self._send(404, {"detail": {"status": "not_found", "message": "no such route"}})

            def do_POST(self):
                fake.keys.append(self.headers.get("xi-api-key"))
                u = urlparse(self.path)
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                voice = u.path.split("/")[3]
                fmt = parse_qs(u.query).get("output_format", ["mp3_44100_128"])[0]
                if voice == VOICE_401:
                    return self._send(401, {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}})
                if voice == VOICE_QUOTA:
                    return self._send(401, {"detail": {"status": "quota_exceeded",
                                                       "message": "This request exceeds your quota."}})
                if fake.refuse_48k and fmt == "pcm_48000":
                    return self._send(403, {"detail": {"status": "output_format_not_allowed",
                                                       "message": "pcm_48000 needs a higher tier"}})
                fake.requests.append(body)
                fake.formats.append(fmt)
                pcm, alignment = speech(body["text"], int(fmt.split("_")[1]))
                self._send(200, {"audio_base64": base64.b64encode(pcm).decode(),
                                 "alignment": None if voice == VOICE_NOALIGN else alignment},
                           {"character-cost": str(len(body["text"])), "request-id": f"req{len(fake.requests)}"})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
