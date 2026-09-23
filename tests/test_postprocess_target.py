import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from postprocess_clip import resolve_target


def test_no_cap_pads_to_narration():
    assert resolve_target(clip_len=8.0, narration=10.0) == 10.0
    assert resolve_target(clip_len=12.0, narration=10.0, tail_pad=0.7) == 12.7


def test_cap_trims_still_tail_after_narration():
    # raw clip idles 3s past the narration; cap keeps only 0.4s of it
    assert resolve_target(clip_len=13.0, narration=10.0, tail_cap=0.4) == 10.4


def test_cap_never_cuts_a_late_action():
    # last on-screen action finished at 12.5s — the floor is 12.5 + 0.6
    assert resolve_target(clip_len=13.0, narration=10.0, tail_cap=0.4, actions_end=12.5) == 13.0
    # ...but never beyond the actual clip length either
    assert resolve_target(clip_len=13.0, narration=10.0, tail_cap=0.4, actions_end=14.0) == 13.0


def test_minimum_breath_after_narration():
    assert resolve_target(clip_len=10.02, narration=10.0, tail_cap=0.4) == 10.15
