"""tts_elevenlabs.py against an offline fake of the API (no network, no billing)."""
import json
import os
import subprocess
import sys
import wave

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import fake_elevenlabs as fe  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
TTS = os.path.join(ROOT, "scripts", "tts_elevenlabs.py")
SECRET = "dummy-test-key-do-not-print-0123456789"


def _scene(sid, narration, **extra):
    return dict({"id": sid, "narration": narration, "intent": "x",
                 "actions": [{"type": "wait", "target": "t", "text": "1"}],
                 "hold_after_ms": 1, "verify": {"expect_on_screen": "y"}}, **extra)


SCENES = [
    _scene("01", "Welcome to the overview.\nIn this video I am going to show you what TruSEO is."),
    _scene("02", "Let's open the Basics tab. The checks are sorted into two groups here."),
    _scene("03", "That's it!\nYou now know how to read the score it gives your content."),
]


def _run_dir(tmp_path, cfg=None, scenes=None):
    (tmp_path / "script.json").write_text(json.dumps(
        {"title": "T", "resolution": "1920x1080", "fps": 30, "voice": "af_heart",
         "scenes": scenes or SCENES}))
    (tmp_path / "config.json").write_text(json.dumps(dict({"elevenlabs_voice": fe.VOICE_OK}, **(cfg or {}))))
    return tmp_path


def _tts(run, fake, *args, key=SECRET, check=True):
    env = dict(os.environ, ELEVENLABS_API_BASE=fake.url)
    env.pop("ELEVENLABS_API_KEY", None)
    if key is not None:
        env["ELEVENLABS_API_KEY"] = key
    p = subprocess.run([sys.executable, TTS, "--run-dir", str(run), *args],
                       env=env, capture_output=True, text=True)
    if check:
        assert p.returncode == 0, p.stdout + p.stderr
    return p


@pytest.fixture
def fake():
    f = fe.Fake()
    yield f
    f.close()


def _secs(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate(), w.getframerate()


def test_contract_wavs_durations_meta_and_words(tmp_path, fake):
    run = _run_dir(tmp_path)
    p = _tts(run, fake)
    audio = run / "audio"
    durations = json.loads((audio / "durations.json").read_text())
    meta = json.loads((audio / "tts_meta.json").read_text())
    assert set(durations) == set(meta) == {"01", "02", "03"}
    for sid in durations:
        secs, sr = _secs(audio / f"{sid}.wav")
        assert sr == 48000 and abs(secs - durations[sid]) < 0.01
        words = json.loads((audio / "words" / f"{sid}.json").read_text())
        assert words["hash"] == meta[sid]["hash"] and words["source"] == "elevenlabs"
        assert [w["word"] for w in words["words"]] == meta[sid]["ref_text"].split()
        assert all(0 <= w["start"] <= w["end"] <= secs + 1e-6 for w in words["words"])
    assert meta["01"]["engine"] == "elevenlabs" and meta["01"]["model"] == "eleven_v4"
    assert len(fake.requests) == 3
    assert "billed" in p.stdout
    assert SECRET not in p.stdout + p.stderr
    assert SECRET not in "".join(f.read_text(errors="ignore") for f in audio.rglob("*.json"))
    assert set(fake.keys) == {SECRET}


def test_neighbouring_scenes_are_sent_as_context(tmp_path, fake):
    _tts(_run_dir(tmp_path), fake)
    first, mid, last = fake.requests
    assert "previous_text" not in first and "Basics tab" in first["next_text"]
    assert "TruSEO" in mid["previous_text"] and "That's it!" in mid["next_text"]
    assert "next_text" not in last
    off = fe.Fake()
    try:
        d = tmp_path / "noctx"
        d.mkdir()
        _tts(_run_dir(d, {"elevenlabs_context": False}), off)
        assert all("previous_text" not in r and "next_text" not in r for r in off.requests)
    finally:
        off.close()


def test_second_run_bills_nothing_and_pacing_changes_reuse_the_take(tmp_path, fake):
    run = _run_dir(tmp_path)
    _tts(run, fake)
    before, _ = _secs(run / "audio" / "02.wav")
    assert len(fake.requests) == 3
    p = _tts(run, fake)
    assert len(fake.requests) == 3 and "billed 0 characters" in p.stdout
    # longer pauses: same cached take, longer audio, still nothing billed
    cfg = json.loads((run / "config.json").read_text())
    cfg.update({"tts_sentence_gap_s": 1.2, "tts_min_words": 0})
    (run / "config.json").write_text(json.dumps(cfg))
    _tts(run, fake)
    after, _ = _secs(run / "audio" / "02.wav")
    assert len(fake.requests) == 3
    assert after > before + 0.5
    usage = json.loads((run / "audio" / "elevenlabs_usage.json").read_text())
    assert usage["total_chars"] == sum(len(r["text"]) for r in fake.requests)


def test_paragraph_break_gets_its_minimum_pause(tmp_path, fake):
    run = _run_dir(tmp_path, {"tts_paragraph_gap_s": 1.0, "tts_lead_s": 0.2, "tts_tail_s": 0.2})
    _tts(run, fake, "--scene-id", "03")
    words = json.loads((run / "audio" / "words" / "03.json").read_text())["words"]
    gap = words[2]["start"] - words[1]["end"]          # "it!" -> "You"
    assert 0.95 <= gap <= 1.2
    assert json.loads((run / "audio" / "durations.json").read_text()).keys() == {"03"}


def test_estimate_makes_no_speech_calls(tmp_path, fake):
    run = _run_dir(tmp_path)
    p = _tts(run, fake, "--estimate")
    total = sum(len(s["narration"]) for s in SCENES)
    assert f"{total} characters would be billed" in p.stdout
    assert fake.requests == [] and not (run / "audio" / "01.wav").exists()
    _tts(run, fake, "--scene-id", "01")
    p = _tts(run, fake, "--estimate")
    assert "1 scene(s) already cached" in p.stdout


def test_force_takes_a_new_paid_take_for_one_scene(tmp_path, fake):
    run = _run_dir(tmp_path)
    _tts(run, fake)
    _tts(run, fake, "--force", "--scene-id", "02")
    assert len(fake.requests) == 4 and fake.requests[-1]["text"].startswith("Let's open")


def test_pronunciation_overrides_are_sent_but_words_keep_the_script_spelling(tmp_path, fake):
    run = _run_dir(tmp_path, {"tts_spoken": {"TruSEO": "True SEO"}})
    _tts(run, fake, "--scene-id", "01")
    assert "True SEO" in fake.requests[0]["text"] and "TruSEO" not in fake.requests[0]["text"]
    words = json.loads((run / "audio" / "words" / "01.json").read_text())["words"]
    assert "TruSEO" in [w["word"] for w in words]
    meta = json.loads((run / "audio" / "tts_meta.json").read_text())["01"]
    assert "TruSEO" in meta["ref_text"] and "True SEO" in meta["tts_text"]


def test_lexicon_ipa_is_sent_in_slashes_on_v4_and_skipped_elsewhere(tmp_path, fake):
    run = _run_dir(tmp_path, {"lexicon": {"TruSEO": "tɹuˌɛsˌiˈoʊ"}})
    _tts(run, fake, "--scene-id", "01")
    assert "/tɹuˌɛsˌiˈoʊ/" in fake.requests[0]["text"]
    d = tmp_path / "v2"
    d.mkdir()
    run2 = _run_dir(d, {"lexicon": {"TruSEO": "tɹuˌɛsˌiˈoʊ"}, "elevenlabs_model": "eleven_multilingual_v2"})
    p = _tts(run2, fake, "--scene-id", "01")
    assert "TruSEO" in fake.requests[1]["text"] and "does not read IPA" in p.stdout


def test_speed_is_native_on_v2_and_local_on_v4(tmp_path, fake):
    slow = [_scene("01", SCENES[1]["narration"], tts_speed=0.8)]
    run = _run_dir(tmp_path, {"elevenlabs_model": "eleven_multilingual_v2"}, slow)
    _tts(run, fake)
    assert fake.requests[0]["voice_settings"]["speed"] == 0.8
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    d = tmp_path / "v4"
    d.mkdir()
    base = _run_dir(d, None, [_scene("01", SCENES[1]["narration"])])
    _tts(base, fake)
    normal, _ = _secs(base / "audio" / "01.wav")
    d2 = tmp_path / "v4slow"
    d2.mkdir()
    run4 = _run_dir(d2, None, slow)
    _tts(run4, fake)
    assert "voice_settings" not in fake.requests[-1]
    slowed, _ = _secs(run4 / "audio" / "01.wav")
    assert slowed > normal * 1.1
    words = json.loads((run4 / "audio" / "words" / "01.json").read_text())["words"]
    assert words[-1]["end"] <= slowed + 1e-6


def test_voice_name_is_resolved_and_remembered(tmp_path, fake):
    run = _run_dir(tmp_path, {"elevenlabs_voice": "eric"})
    _tts(run, fake, "--scene-id", "01")
    saved = json.loads((run / "audio" / "elevenlabs_voice.json").read_text())
    assert saved["eric"]["id"] == fe.VOICE_OK
    p = _tts(_run_dir(tmp_path, {"elevenlabs_voice": "Nobody"}), fake, check=False)
    assert p.returncode == 2 and "Eric" in p.stderr and "Sarah" in p.stderr


def test_format_falls_back_when_the_plan_refuses_48k(tmp_path):
    f = fe.Fake(refuse_48k=True)
    try:
        run = _run_dir(tmp_path)
        p = _tts(run, f, "--scene-id", "01")
        assert f.formats == ["pcm_24000"] and "pcm_24000" in p.stdout
        assert _secs(run / "audio" / "01.wav")[1] == 24000
    finally:
        f.close()


def test_missing_alignment_still_delivers_audio_without_word_file(tmp_path, fake):
    run = _run_dir(tmp_path, {"elevenlabs_voice": fe.VOICE_NOALIGN, "tts_max_attempts": 2})
    p = _tts(run, fake, "--scene-id", "01")
    assert (run / "audio" / "01.wav").exists()
    assert not (run / "audio" / "words" / "01.json").exists()
    assert "time it by ear" in p.stdout and len(fake.requests) == 2  # one retry, then accept


@pytest.mark.parametrize("voice,code,needle", [
    (fe.VOICE_401, 4, "rejected the API key"),
    (fe.VOICE_QUOTA, 5, "quota exhausted"),
])
def test_api_failures_are_actionable_and_never_leak_the_key(tmp_path, fake, voice, code, needle):
    p = _tts(_run_dir(tmp_path, {"elevenlabs_voice": voice}), fake, check=False)
    assert p.returncode == code and needle in p.stderr
    assert SECRET not in p.stdout + p.stderr


def test_missing_key_explains_the_env_var(tmp_path, fake):
    p = _tts(_run_dir(tmp_path, {"elevenlabs_key_env": "MY_EL_KEY"}), fake, key=None, check=False)
    assert p.returncode == 4 and "$MY_EL_KEY" in p.stderr and "never put the key in" in p.stderr
    assert fake.requests == []


def test_list_voices_and_audition(tmp_path, fake):
    env = dict(os.environ, ELEVENLABS_API_BASE=fake.url, ELEVENLABS_API_KEY=SECRET)
    p = subprocess.run([sys.executable, TTS, "--list-voices"], env=env, capture_output=True, text=True, check=True)
    assert fe.VOICE_OK in p.stdout and "Sarah" in p.stdout
    out = tmp_path / "aud"
    p = subprocess.run([sys.executable, TTS, "--audition", "A short sample line.", "--voices", "Eric,Sarah",
                        "--out", str(out)], env=env, capture_output=True, text=True, check=True)
    assert sorted(x.name for x in out.iterdir()) == ["1-eric.wav", "2-sarah.wav"]
    assert "billed 40 characters" in p.stdout
