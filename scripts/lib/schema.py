"""Pure-Python validation for the scene script contract.

Two phases share one schema:
  * pre-discovery  (``discovered=False``): ``selector`` / ``focus_selector`` may be null.
  * post-discovery (``discovered=True``):  every ``selector`` and ``focus_selector``
    must be a non-null string (recording reads only the discovered file).

``validate_script`` returns a list of human-readable error strings;
an empty list means valid.
"""

ACTION_TYPES = {"click", "type", "scroll", "hover", "wait", "goto"}


def validate_script(obj, discovered=False):
    errors = []
    if not isinstance(obj, dict):
        return ["root: must be a JSON object"]

    for key in ("title", "resolution", "fps", "voice", "scenes"):
        if key not in obj:
            errors.append(f"root: missing required key '{key}'")

    res = obj.get("resolution")
    if res is not None and not (isinstance(res, str) and "x" in res):
        errors.append("root.resolution: must be a string like '1920x1080'")
    if "fps" in obj and not isinstance(obj["fps"], int):
        errors.append("root.fps: must be an integer")

    scenes = obj.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        errors.append("root.scenes: must be a non-empty array")
        return errors

    seen_ids = set()
    for i, sc in enumerate(scenes):
        loc = f"scenes[{i}]"
        if not isinstance(sc, dict):
            errors.append(f"{loc}: must be an object")
            continue
        for key in ("id", "narration", "intent", "actions", "hold_after_ms", "verify"):
            if key not in sc:
                errors.append(f"{loc}: missing required key '{key}'")
        sid = sc.get("id")
        if sid in seen_ids:
            errors.append(f"{loc}.id: duplicate id '{sid}'")
        seen_ids.add(sid)
        if "hold_after_ms" in sc and not isinstance(sc["hold_after_ms"], int):
            errors.append(f"{loc}.hold_after_ms: must be an integer")

        verify = sc.get("verify")
        if not isinstance(verify, dict) or not verify.get("expect_on_screen"):
            errors.append(f"{loc}.verify.expect_on_screen: required non-empty string")

        actions = sc.get("actions")
        if not isinstance(actions, list) or not actions:
            errors.append(f"{loc}.actions: must be a non-empty array")
        else:
            for j, ac in enumerate(actions):
                aloc = f"{loc}.actions[{j}]"
                if not isinstance(ac, dict):
                    errors.append(f"{aloc}: must be an object")
                    continue
                if ac.get("type") not in ACTION_TYPES:
                    errors.append(f"{aloc}.type: must be one of {sorted(ACTION_TYPES)}")
                if not ac.get("target"):
                    errors.append(f"{aloc}.target: required human-language description")
                if ac.get("type") == "type" and not ac.get("text"):
                    errors.append(f"{aloc}.text: required when type=='type'")
                if discovered and not ac.get("selector"):
                    errors.append(f"{aloc}.selector: must be resolved (non-null) after discovery")

        if discovered and not sc.get("focus_selector"):
            errors.append(f"{loc}.focus_selector: must be resolved (non-null) after discovery")

    return errors
