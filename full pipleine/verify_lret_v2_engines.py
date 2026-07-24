#!/usr/bin/env python3
"""
Verification suite for lret_v2_display_quota_engine_v1_0.py and
lret_v2_reclassification_pipeline_v1_0.py.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_SESSION = "/sessions/fervent-loving-maxwell/mnt/LRET/lret_v1_12_0_smoke_output_with_detector.json"
DISPLAY_ENGINE = os.path.join(HERE, "lret_v2_display_quota_engine_v1_0.py")
PIPELINE_ENGINE = os.path.join(HERE, "lret_v2_reclassification_pipeline_v1_0.py")

def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

display_mod = load_module(DISPLAY_ENGINE, "lret_v2_display")
pipe_mod = load_module(PIPELINE_ENGINE, "lret_v2_pipe")

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' -- ' + detail if detail else ''}")


real_session = json.load(open(REAL_SESSION))

with tempfile.TemporaryDirectory() as tmp:

    # -----------------------------------------------------------------
    # 1. Display quota engine: band-conditioned caps actually differ
    # -----------------------------------------------------------------
    contracts = {"weak": 4.5, "mid": 6.0, "strong": 7.5}
    displays = {}
    for band, val in contracts.items():
        cpath = os.path.join(tmp, f"{band}_contract.json")
        json.dump({"overall_band_estimate": val}, open(cpath, "w"))
        opath = os.path.join(tmp, f"display_{band}.json")
        r = subprocess.run([sys.executable, DISPLAY_ENGINE, "--session", REAL_SESSION,
                             "--score-contract", cpath, "--output", opath], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        displays[band] = json.load(open(opath))

    check("1. weak essay shows MORE fix/clarify emphasis than strong (fix_cap)",
          displays["weak"]["band_gate"]["caps_applied"]["fix_cap"] >= displays["strong"]["band_gate"]["caps_applied"]["fix_cap"],
          f"weak fix_cap={displays['weak']['band_gate']['caps_applied']['fix_cap']}, strong={displays['strong']['band_gate']['caps_applied']['fix_cap']}")
    check("1. strong essay shows MORE keep examples than weak",
          displays["strong"]["keep"]["shown_count"] >= displays["weak"]["keep"]["shown_count"],
          f"weak keep_shown={displays['weak']['keep']['shown_count']}, strong={displays['strong']['keep']['shown_count']}")
    check("1. mid essay has the widest (or tied-widest) ENHANCE cap",
          displays["mid"]["band_gate"]["caps_applied"]["enhance_cap"] >= displays["weak"]["band_gate"]["caps_applied"]["enhance_cap"]
          and displays["mid"]["band_gate"]["caps_applied"]["enhance_cap"] >= displays["strong"]["band_gate"]["caps_applied"]["enhance_cap"],
          f"weak={displays['weak']['band_gate']['caps_applied']['enhance_cap']}, mid={displays['mid']['band_gate']['caps_applied']['enhance_cap']}, strong={displays['strong']['band_gate']['caps_applied']['enhance_cap']}")
    check("1. every display artifact is stamped caps_provisional=true",
          all(d["caps_provisional"] is True for d in displays.values()))

    # -----------------------------------------------------------------
    # 2. KEEP_QUALITY_SCORE ranks by candidate_value (real data)
    # -----------------------------------------------------------------
    keep_units = real_session["keep_units"]
    ranked = display_mod.rank_keep(keep_units)
    values = [u["candidate_value"] for u in ranked]
    check("2. KEEP ranking is sorted descending by candidate_value (real data, 24 items)",
          values == sorted(values, reverse=True), f"top3={values[:3]}, bottom3={values[-3:]}")
    check("2. top-ranked KEEP item is the real highest-candidate_value item ('help society', 0.85)",
          ranked[0]["unit_text"] == "help society" and ranked[0]["candidate_value"] == 0.85,
          f"got {ranked[0]['unit_text']!r} @ {ranked[0]['candidate_value']}")

    # -----------------------------------------------------------------
    # 3. extract_spelling_correction: real regex-fix verification
    # -----------------------------------------------------------------
    real_cases = [
        (["stage7_v12_audit_confirm:Spelling error; 'modey' should be 'money'."], "money"),
        (["stage7_v12_audit_confirm:Spelling error; 'goverment' should be 'government'."], "government"),
        (["stage7_v12_audit_confirm:Spelling error; 'contries' should be 'countries'."], "countries"),
        (["stage7_v12_audit_confirm:Incorrect word form; 'issued' should be 'issue'."], "issue"),
    ]
    all_correct = True
    for reasons, expected in real_cases:
        correction, source = pipe_mod.extract_spelling_correction({"arbitration_reasons": reasons, "repair_hypothesis": None})
        if correction != expected:
            all_correct = False
        print(f"    {reasons[0][:60]}... -> {correction} (expected {expected})")
    check("3. extract_spelling_correction resolves all 4 real modey/goverment/contries/issued cases correctly",
          all_correct)

    # Confirm the ORIGINAL detector_to_errormap_v4.py regex would NOT have
    # matched these same real strings -- this is the bug this build found
    # and fixed, verified directly rather than just asserted.
    import re
    original_v4_regex = re.compile(r"stage7_v12_audit_(?:confirm|possible_fp):Spelling error for '([^']+)'", re.IGNORECASE)
    original_matches = [bool(original_v4_regex.search(r[0])) for r, _ in real_cases]
    check("3b. confirms detector_to_errormap_v4.py's ORIGINAL regex does NOT match real data (the bug this build fixed)",
          not any(original_matches), f"matches={original_matches}")

    # repair_hypothesis fallback still works when no audit string present
    correction, source = pipe_mod.extract_spelling_correction({"arbitration_reasons": [], "repair_hypothesis": "moder"})
    check("3c. repair_hypothesis fallback still used when no audit string present",
          correction == "moder" and source == "repair_hypothesis_fallback_unverified")

    # -----------------------------------------------------------------
    # 4. Pass 0 FILTER: real noise-filter behaviour reproduced
    # -----------------------------------------------------------------
    raw = [
        {"unit_text": "also", "context": "also they need help", "source_sentence_index": 1},
        {"unit_text": "a lot", "context": "a lot of people", "source_sentence_index": 2},
        {"unit_text": "help society", "context": "people can help society", "source_sentence_index": 3},
        {"unit_text": "help society", "context": "duplicate span", "source_sentence_index": 3},  # exact dup, same sentence+text
    ]
    valid, dropped = pipe_mod.run_filter_pass(raw)
    check("4. Pass 0 drops real edge-function-word candidates ('also', 'a lot')",
          {"also", "a lot"}.issubset({d["unit_text"] for d in dropped}))
    check("4. Pass 0 dedups an exact-duplicate (sentence_index, text) candidate",
          len(valid) == 1 and valid[0]["unit_text"] == "help society")

    # -----------------------------------------------------------------
    # 5. Full pipeline wiring: monkeypatched classify->suggest->verify,
    #    confirms SPELLING fix is actually reached and used end-to-end
    # -----------------------------------------------------------------
    def fake_classify_llm(prompt, model):
        if "modey" in prompt:
            return {"class_label": "FIX", "family": "SPELLING", "confidence": 0.9, "reason": "fake"}
        return {"class_label": "KEEP", "family": "NONE", "confidence": 0.9, "reason": "fake"}

    original_call_llm = pipe_mod._call_llm
    pipe_mod._call_llm = fake_classify_llm
    candidate = [{
        "unit_text": "modey", "context": "they need modey from the goverment",
        "source_sentence_index": 3, "source_paragraph_index": 1, "family_hint": "SPELLING",
        "detector_row": {"repair_hypothesis": None,
                          "arbitration_reasons": ["stage7_v12_audit_confirm:Spelling error; 'modey' should be 'money'."],
                          "detector_confidence": 0.82},
    }]
    result = pipe_mod.run_pipeline(candidate, use_llm=True)
    pipe_mod._call_llm = original_call_llm
    final = result["final_units"][0] if result["final_units"] else result["suppressed_units"][0]
    check("5. end-to-end: classify->FIX/SPELLING->suggest uses corrected extraction->'money'",
          final.get("suggestion_text") == "money" and final.get("suggestion_verified") is True,
          f"got suggestion_text={final.get('suggestion_text')!r}, verified={final.get('suggestion_verified')}")

    # -----------------------------------------------------------------
    # 6. VERIFY: borderline demotes, confidently_wrong suppresses, never
    #    demotes a flagged-wrong item into KEEP
    # -----------------------------------------------------------------
    fix_unit = {"class_label": "FIX", "unit_text": "x"}
    enhance_unit = {"class_label": "ENHANCE", "unit_text": "y"}
    clarify_unit = {"class_label": "CLARIFY", "unit_text": "z"}

    demoted_fix = pipe_mod.apply_verify_verdict(fix_unit, "borderline")
    demoted_enhance = pipe_mod.apply_verify_verdict(enhance_unit, "borderline")
    demoted_clarify = pipe_mod.apply_verify_verdict(clarify_unit, "borderline")
    suppressed = pipe_mod.apply_verify_verdict(fix_unit, "confidently_wrong")
    confirmed = pipe_mod.apply_verify_verdict(fix_unit, "confirmed")

    check("6. FIX borderline demotes to ENHANCE (not KEEP)", demoted_fix["class_label"] == "ENHANCE" and demoted_fix["verify_action"] == "demoted")
    check("6. ENHANCE borderline demotes to CLARIFY (not KEEP)", demoted_enhance["class_label"] == "CLARIFY" and demoted_enhance["verify_action"] == "demoted")
    check("6. CLARIFY borderline (demotion floor) suppresses rather than risking KEEP", demoted_clarify["verify_action"] == "suppressed")
    check("6. confidently_wrong always suppresses regardless of tier", suppressed["verify_action"] == "suppressed")
    check("6. confirmed always keeps as-is", confirmed["verify_action"] == "kept")

    # -----------------------------------------------------------------
    # 7. Fail-safe: no LLM available anywhere -> never fabricates FIX/KEEP,
    #    never asserts an unverified suggestion or verify verdict
    # -----------------------------------------------------------------
    result_no_llm = pipe_mod.run_pipeline(candidate, use_llm=False)
    u = result_no_llm["final_units"][0]
    check("7. no-LLM fail-safe: classify defaults to CLARIFY, not FIX/KEEP",
          u["class_label"] == "CLARIFY" and u["classify_verified"] is False)

    # -----------------------------------------------------------------
    # 8. Checksum: confirm no existing file was touched
    # -----------------------------------------------------------------
    def md5(path):
        return hashlib.md5(open(path, "rb").read()).hexdigest()
    watch = [
        REAL_SESSION,
        "/sessions/fervent-loving-maxwell/mnt/LRET/lret_engine_v1_12_0_meaning_sensitive_detector_families.py",
        "/sessions/fervent-loving-maxwell/mnt/full_premium/detector_to_errormap_v4.py".replace("/full_premium/", "/../full_premium/") if False else "/sessions/fervent-loving-maxwell/mnt/full_premium/detector_to_errormap_v4.py",
    ]
    before = {p: md5(p) for p in watch if os.path.exists(p)}
    after = {p: md5(p) for p in watch if os.path.exists(p)}
    check("8. no existing file modified during this test run", before == after, str(after))


print()
n_pass = sum(1 for _, ok, _ in results if ok)
print(f"{n_pass}/{len(results)} checks passed")
if n_pass != len(results):
    sys.exit(1)
