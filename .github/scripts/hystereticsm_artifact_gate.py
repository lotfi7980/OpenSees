"""Upload to .github/scripts/hystereticsm_artifact_gate.py.

Self-contained Linux adapter of the two existing STMF material gates. No build,
member analysis, physical fit, source patch or import of a pip OpenSees package.
Only API/output plumbing changes: native opensees.so; AllData 48 instead of 43;
archived Windows responses are NEVER reused for acceptance of this new binary.

Private damage variables such as CdamfcPeak are not exposed by AllData. We compare
all exposed state/envelope/energy and subsequent responses, not an invented FI.
Raw testUniaxialMaterial/setStrain commits each call and exposes no native revert
API in the old harness. Revert is exercised through native Domain failure in
DIRECT_ELEMENT, FIBER and FIBER_AGGREGATOR, exactly as in the earlier controls.
"""
from __future__ import annotations
import argparse
import base64
import csv
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import traceback
import urllib.parse
import urllib.request

REPO = "lotfi7980/OpenSees"
RUN_ID = 40186529499
RUN_ATTEMPT = 1
ARTIFACT_NAME = "OpenSeesPy-Fixed-Ubuntu"
ARTIFACT_ID = 12596650295
ARTIFACT_DIGEST = "sha256:d04ff22d63818e8a8e0a31b656d67e121ef97bc3412a48c3e08a286eadbc782f"
BUILDER_HEAD = "1b351e1c0971c315d6dcadffa00b2f88f4e640b2"
BUILDER_PATH = ".github/workflows/build_hystereticsm_fixed.yml"
BUILDER_RECIPE_SHA256 = "d1b6e6d7e2a378883ee7c334d5c23d21fc7073e0a84c27eaa20a9c19d488e3a9"
SOURCE_SHA = "a3ece539b412d760cfba400de0a17a3986a687e5"
MATERIAL_CPP_SHA256 = "14378d6a1aea2c6b1a744853c95a7d2c77b25286be45aaa9e329d13ce198e943"

# Literal numerical data read from the frozen N_OPT01 envelope, not refitted.
# Pair ordering is stress (MPa), strain. h/Lp remains 100 mm in the parent law.
ENVELOPE = [
    [396.75, 0.00198375], [444.36, 0.015],
    [614.9624999999263, 0.11699746065072468],
    [192.06223474834334, 0.26084990417341414],
    [192.06223474834334, 0.8],
]
ANCHORS = [0., .006, -.006, .02, -.02, .04, -.04, .04, -.04, .04, -.04, 0.]
STEP = .00025
OPTIONS = {
    "DAMAGE": ["-damage", .002, .002],
    "DAMAGE_DEGENV": ["-damage", .002, .002, "-degEnv", .5, .5],
}
PATHS = ("RAW", "DIRECT_ELEMENT", "FIBER", "FIBER_AGGREGATOR")
ABS_TOL = 1e-9
REL_TOL = 1e-9
ENV_TOL = 1e-8
FORCE_TOL = 1e-8
ENERGY_TOL = 1e-8
VIRGIN_TOL = 1e-8 * ENVELOPE[2][0]
OLD_PROVENANCE = {
    "frozen_source_sha256": "77d312acaff60a0ab2a8ac443499ea99bef84f5e420a80410ec5958d45fe41a0",
    "material_gate_py_sha256": "8a738a73f6dec7a8c55a48ed74113d547df138d837d80b513bf2f993f6cbb150",
    "compatibility_py_sha256": "68572e6ceb1787810d0221d3a50ce580c05778efe7dfef0f454d521686010a61",
    "old_preregistration_sha256": "6c1ed0b607db789a9d8a52f2100268a2d61bbe9bdeba2941e3fe671a67587a14",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(out, name, value):
    (out / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def load(out, name):
    return json.loads((out / name).read_text(encoding="utf-8"))


def table(out, name, rows):
    if not rows:
        return
    with (out / name).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
        writer.writeheader()
        writer.writerows(rows)


def github(route):
    # Credential is sent only to the GitHub API, never written to an output file.
    headers = {"User-Agent": "STMF-HystereticSM-gate", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request("https://api.github.com" + route, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def content(repo, path, ref):
    item = github(f"/repos/{repo}/contents/{path}?ref={urllib.parse.quote(ref)}")
    require(item.get("encoding") == "base64", "Unexpected GitHub contents encoding")
    return base64.b64decode(item["content"])


def audit_run(out):
    run = github(f"/repos/{REPO}/actions/runs/{RUN_ID}")
    require(run["id"] == RUN_ID and run["run_attempt"] == RUN_ATTEMPT, "Wrong producer run/attempt")
    require(run["repository"]["full_name"] == REPO, "Wrong producer repository")
    require(run["status"] == "completed" and run["conclusion"] == "success", "Producer run not successful")
    require(run["head_sha"] == BUILDER_HEAD and run["path"] == BUILDER_PATH, "Producer workflow revision changed")
    artifacts = github(f"/repos/{REPO}/actions/runs/{RUN_ID}/artifacts?per_page=100")["artifacts"]
    found = [a for a in artifacts if a["id"] == ARTIFACT_ID and a["name"] == ARTIFACT_NAME]
    require(len(found) == 1 and not found[0]["expired"], "Exact artifact missing or expired")
    require(found[0].get("digest") == ARTIFACT_DIGEST, "Artifact digest differs from audited producer")
    recipe = content(REPO, BUILDER_PATH, BUILDER_HEAD)
    require(hashlib.sha256(recipe).hexdigest() == BUILDER_RECIPE_SHA256, "Build recipe checksum differs")
    require(SOURCE_SHA.encode() in recipe and b'python-version: "3.11"' in recipe and b"ubuntu-22.04" in recipe,
            "Build recipe not pinned to expected source/platform")
    cpp = content("OpenSees/OpenSees", "SRC/material/uniaxial/HystereticSMMaterial.cpp", SOURCE_SHA)
    require(hashlib.sha256(cpp).hexdigest() == MATERIAL_CPP_SHA256, "Official material source checksum differs")
    for marker in (b"Ttangent = Ctangent", b"return this->revertToLastCommit();", b"revertEnvelopeToCommit();",
                   b"degEnvArray, rotYArray, forceLimitStates, defoLimitStates"):
        require(marker in cpp, "Required official source marker absent: " + marker.decode())
    (out / "producer-workflow.yml").write_bytes(recipe)
    dump(out, "PROVENANCE.json", {
        "pass": True, "producer_run": RUN_ID, "producer_run_attempt": RUN_ATTEMPT,
        "producer_url": run["html_url"], "producer_workflow_head": BUILDER_HEAD,
        "compiled_source_commit": SOURCE_SHA, "artifact_id": ARTIFACT_ID,
        "artifact_name": ARTIFACT_NAME, "artifact_archive_digest": ARTIFACT_DIGEST,
        "build_recipe_sha256": BUILDER_RECIPE_SHA256, "official_cpp_sha256": MATERIAL_CPP_SHA256,
        "commit_verification": "Immutable artifact/run plus pinned, checksum-verified build recipe; NOT an embedded binary commit attestation",
        "limitation": "Producer artifact has only opensees.so and SHA256.txt; no compiler manifest, signed source attestation or embedded commit is available.",
    })


def binary_path(root):
    candidates = [p.resolve() for p in root.rglob("opensees.so") if p.is_file()]
    require(len(candidates) == 1, f"Expected one opensees.so, found {len(candidates)}")
    return candidates[0]


def import_binary(path):
    # Deliberate exact-path import: no openseespy/openseespywin pip fallback.
    spec = importlib.util.spec_from_file_location("opensees", path)
    require(spec is not None and spec.loader is not None, "No native module loader")
    module = importlib.util.module_from_spec(spec)
    sys.modules["opensees"] = module
    spec.loader.exec_module(module)
    require(Path(module.__file__).resolve() == path.resolve(), "Loaded a different OpenSees binary")
    return module


def verify_binary(root, out):
    require(load(out, "PROVENANCE.json")["pass"], "Producer provenance not verified")
    require(platform.system() == "Linux" and sys.version_info[:2] == (3, 11), "Requires Linux CPython 3.11")
    require(platform.machine() in ("x86_64", "AMD64"), "Requires x86-64")
    path = binary_path(root)
    require(path.read_bytes()[:4] == b"\x7fELF", "Artifact is not a Linux ELF binary")
    checksum_files = list(root.rglob("SHA256.txt"))
    require(len(checksum_files) == 1, "Producer SHA256.txt missing or ambiguous")
    expected = []
    for line in checksum_files[0].read_text().splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if match and Path(match.group(2)).name == "opensees.so":
            expected.append(match.group(1).lower())
    require(len(expected) == 1 and sha(path) == expected[0], "Binary checksum differs from producer SHA256.txt")
    linked = subprocess.run(["ldd", str(path)], capture_output=True, text=True, check=False)
    (out / "ldd.txt").write_text(linked.stdout + linked.stderr)
    require(linked.returncode == 0 and "not found" not in linked.stdout + linked.stderr,
            "ELF dependency missing; stop, do not install or patch libraries")
    module = import_binary(path)
    dump(out, "BINARY_IMPORT.json", {"pass": True, "path": str(path), "loaded_path": module.__file__,
         "binary_sha256": sha(path), "source_commit_from_provenance": SOURCE_SHA,
         "opensees_version": module.version(), "python": sys.version, "platform": platform.platform(),
         "os_release": Path("/etc/os-release").read_text(), "test_script_sha256": sha(__file__)})


def make_path(np):
    data = [0.]
    markers = []
    for target in ANCHORS[1:]:
        count = int(math.ceil(abs(target - data[-1]) / STEP - 1e-10))
        data.extend(np.linspace(data[-1], target, count + 1)[1:].tolist())
        markers.append(len(data) - 1)
    require(markers[4] == 576 and len(data) == 2337, "Previous registered history changed")
    return data, markers


def rig(ops, path, options, targets):
    ops.wipe()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    flat = [v for pair in ENVELOPE for v in pair]
    command = ["HystereticSM", 1, "-posEnv", *flat, "-negEnv", *[-v for v in flat], *options, "-printInput"]
    parsed = ops.uniaxialMaterial(*command)
    signature = {"command": command, "constructor_return": parsed}
    if path == "RAW":
        ops.testUniaxialMaterial(1)
        return None, signature
    ops.node(1, 0., 0.)
    ops.node(2, 0., 0.)
    ops.fix(1, 1, 1, 1)
    ops.fix(2, 0, 0, 1)
    ops.uniaxialMaterial("Elastic", 2, 1.)
    if path == "DIRECT_ELEMENT":
        ops.element("zeroLength", 1, 1, 2, "-mat", 1, 2, "-dir", 1, 2)
        prefix = ("material", 1)
    else:
        ops.section("Fiber", 1)
        ops.fiber(-.5, 0., .5, 1)
        ops.fiber(.5, 0., .5, 1)
        if path == "FIBER_AGGREGATOR":
            ops.section("Aggregator", 2, 2, "Vy", "-section", 1)
            ops.element("zeroLengthSection", 1, 1, 2, 2)
        elif path == "FIBER":
            ops.element("zeroLengthSection", 1, 1, 2, 1)
            # Exactly the corrected prior fixture: an uncoupled elastic y DOF
            # avoids the all-prescribed-DOF LAPACK failure without another copy
            # of the HystereticSM material. It carries no axial work.
            ops.element("zeroLength", 2, 1, 2, "-mat", 2, "-dir", 2)
        else:
            raise RuntimeError("Unknown path: " + path)
        prefix = ("section", "fiber", -.5, 0.)
    signature["deformation_limits"] = list(ops.eleResponse(1, *prefix, "defoLimitStates"))
    signature["force_limits"] = list(ops.eleResponse(1, *prefix, "forceLimitStates"))
    ops.timeSeries("Path", 1, "-dt", 1., "-values", *targets, "-useLast")
    ops.pattern("Plain", 1, 1)
    ops.sp(2, 1, 1.)
    ops.constraints("Transformation")
    ops.numberer("Plain")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1e-12, 10)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.)
    ops.analysis("Static")
    return prefix, signature


def state(ops, prefix):
    if prefix is None:
        raw = {"strain": float(ops.getStrain()), "stress_MPa": float(ops.getStress()),
               "tangent_MPa": float(ops.getTangent())}
        require(all(math.isfinite(v) for v in raw.values()), "Nonfinite raw material state")
        return raw
    def response(name):
        return list(ops.eleResponse(1, *prefix, name))
    data = response("AllData")
    require(len(data) == 48, "Unknown native AllData layout; do not reuse old 43-field indices")
    stress, strain, tangent = response("stress")[0], response("strain")[0], response("tangent")[0]
    require(all(math.isfinite(v) for v in data + [stress, strain, tangent]), "Nonfinite native state")
    forces = list(ops.eleForce(1))
    require(len(forces) == 6, "Unexpected 2D element force vector")
    ops.reactions()
    return {"strain": strain, "stress_MPa": stress, "tangent_MPa": tangent,
            "force_j_N": forces[3], "reaction_i_N": ops.nodeReaction(1, 1), "time": ops.getTime(),
            "all_data": data, "native_work_MPa": data[43],
            "force_error_N": abs(forces[3] - stress), "equilibrium_error_N": abs(forces[0] + forces[3])}


def state_delta(a, b):
    fields = ("strain", "stress_MPa", "tangent_MPa", "force_j_N", "reaction_i_N", "time")
    result = {k: abs(a[k] - b[k]) for k in fields}
    result["envelope_max_abs_change"] = max(abs(x-y) for x,y in zip(a["all_data"][1:29], b["all_data"][1:29]))
    # Compare all exposed committed history, including extrema, energy and load
    # indicator; no private crack/FI/damage index is inferred.
    result["exposed_history_max_abs_change"] = max(abs(x-y) for x,y in zip(a["all_data"][39:47], b["all_data"][39:47]))
    result["pass"] = (
        all(result[k] <= ABS_TOL + REL_TOL*max(abs(a[k]),abs(b[k])) for k in fields)
        and result["envelope_max_abs_change"] <= ENV_TOL
        and all(abs(x-y) <= ABS_TOL + REL_TOL*max(abs(x),abs(y))
                for x,y in zip(a["all_data"][29:47], b["all_data"][29:47]))
    )
    return result


def validate_copy(z, option):
    data = z["all_data"]
    expected = [1., 1., .002, .002, 0., *([.5,.5] if option == "DAMAGE_DEGENV" else [0.,0.])]
    require(all(abs(x-y) <= 1e-12 for x,y in zip(data[29:36], expected)),
            "pinch/damage/beta/degEnv changed during material copying")


def sequence(ops, out, path, option, targets, markers, branch):
    prefix, signature = rig(ops, path, OPTIONS[option], targets)
    previous = state(ops, prefix)
    if prefix is not None:
        validate_copy(previous, option)
    history = {}
    rows = []
    recoveries = []
    work = 0.
    checkpoint = markers[4]
    for i in range(1, len(targets)):
        if prefix is None:
            ops.setStrain(targets[i])
        else:
            require(ops.analyze(1) == 0, f"Unexpected convergence failure: {path}/{option}/{branch}/{i}")
        now = state(ops, prefix)
        require(abs(now["strain"] - targets[i]) <= ABS_TOL + REL_TOL*abs(targets[i]), "Actual fiber strain differs from registered history")
        work += .5*(previous["stress_MPa"] + now["stress_MPa"])*(now["strain"] - previous["strain"])
        row = {k:v for k,v in now.items() if k != "all_data"}
        row.update(step=i, path=path, option=option, branch=branch, endpoint=i in markers, work_MPa=work)
        if prefix is not None:
            row["energy_error_MPa"] = abs(work-now["native_work_MPa"])
            row.update({f"native_{j:02d}":v for j,v in enumerate(now["all_data"])})
        rows.append(row)
        history[i] = now
        previous = now
        if i == checkpoint and branch != "continuous":
            require(prefix is not None, "Raw tester has no rejected-step API")
            iterations = int(branch[-1])
            ops.test("NormDispIncr", -1., iterations)
            code = ops.analyze(1)
            after = state(ops, prefix)
            require(code != 0, "Intentionally rejected step was unexpectedly accepted")
            require(abs(ops.getTime()-i) < 1e-12, "Rejected step advanced committed time")
            difference = state_delta(now, after)
            recoveries.append(dict(path=path, option=option, branch=branch, native_code=code, **difference))
            dump(out, f"{path}_{option}_{branch}_STATE.json",
                 {"before": now, "after_revert": after, "signature": signature, "difference": difference})
            ops.test("NormDispIncr", 1e-12, 10)
    table(out, f"{path}_{option}_{branch}.csv", rows)
    ops.wipe()
    return rows, history, recoveries, signature


def run_gate(root, out):
    import numpy as np
    require(np.__version__ == "1.26.4", "Match NumPy version used by previous path generator")
    require(load(out, "BINARY_IMPORT.json")["pass"], "Import/provenance gate not passed")
    path_to_binary = binary_path(root)
    require(sha(path_to_binary) == load(out,"BINARY_IMPORT.json")["binary_sha256"], "Binary changed after import verification")
    ops = import_binary(path_to_binary)
    targets, markers = make_path(np)
    native_fields = ["tag"]
    for side in ("p", "n"):
        for number in range(1,8):
            native_fields.extend([f"mom{number}{side}", f"rot{number}{side}"])
    native_fields += ["pinchX","pinchY","damfc1","damfc2","beta","degEnvp","degEnvn",
                      "rotYp","rotYn","energyA","CrotMax","CrotMin","CrotPu","CrotNu",
                      "CenergyD","CloadIndicator","Cstress","Cstrain","Ttangent"]
    require(len(native_fields) == 48, "Native schema transcription error")
    dump(out,"NATIVE_STATE_SCHEMA.json", {f"native_{i:02d}":name for i,name in enumerate(native_fields)})
    dump(out, "PREREGISTRATION.json", {
        "backbone_stress_strain": ENVELOPE, "Lp_h_mm": 100., "anchors": ANCHORS,
        "step": STEP, "checkpoint": markers[4], "options": OPTIONS, "paths": PATHS,
        "virgin_max_strain": .3, "virgin_intervals": 1200,
        "state_tolerance_absolute": ABS_TOL, "state_tolerance_relative": REL_TOL,
        "envelope_tolerance_absolute": ENV_TOL, "force_tolerance_N": FORCE_TOL,
        "energy_tolerance_MPa": ENERGY_TOL, "virgin_stress_tolerance_MPa": VIRGIN_TOL,
        "old_gate_provenance": OLD_PROVENANCE, "source_commit": SOURCE_SHA,
        "adaptations_only": ["Exact-path Linux native import", "48-field AllData layout from pinned official source",
                             "No Windows archive reuse for a new binary", "Self-contained frozen envelope literals"],
        "limitations": ["Not experimental validation", "Not a forceBeamColumn member test",
                        "Raw tester has no explicit revert; native Domain rollback tested in all three element routes",
                        "Private CdamfcPeak has no recorder; all exposed history/envelope and future response are checked"],
    })
    reference_rows = {}
    reversions = []
    continuations = []
    signatures = {}
    all_accepted = []
    for path in PATHS:
        for option in OPTIONS:
            reference = None
            branches = ("continuous",) if path == "RAW" else ("continuous","failed1","failed3")
            for branch in branches:
                rows, states, recovery, signature = sequence(ops,out,path,option,targets,markers,branch)
                all_accepted.extend(rows)
                reversions.extend(recovery)
                signatures[path+"_"+option] = signature
                if branch == "continuous":
                    reference = states
                    reference_rows[path,option] = rows
                else:
                    differences = [state_delta(states[k],reference[k]) for k in states if k > markers[4]]
                    continuations.append(dict(path=path,option=option,branch=branch,
                        pass_all=all(v["pass"] for v in differences),
                        **{k:max(v[k] for v in differences) for k in differences[0] if k != "pass"}))
    table(out,"REVERT_COMPARISON.csv",reversions)
    table(out,"CONTINUATION_COMPARISON.csv",continuations)
    dump(out,"SIGNATURES.json",signatures)
    effects = []
    for path in PATHS:
        a = reference_rows[path,"DAMAGE"]
        b = reference_rows[path,"DAMAGE_DEGENV"]
        delta = max(abs(x["stress_MPa"]-y["stress_MPa"]) for x,y in zip(a,b))
        # Detect a resolved independent effect using the existing numerical
        # comparison tolerance, not a newly fitted physical magnitude.
        resolved = any(abs(x["stress_MPa"]-y["stress_MPa"]) > ABS_TOL + REL_TOL*max(abs(x["stress_MPa"]),abs(y["stress_MPa"])) for x,y in zip(a,b))
        at_amplitude = [i for i,r in enumerate(a) if abs(r["strain"]-.04) < 1e-10]
        effects.append(dict(path=path, independent_degenv_effect_pass=resolved,
            max_stress_effect_MPa=delta, first_positive_peak_damage_MPa=a[at_amplitude[0]]["stress_MPa"],
            last_positive_peak_damage_MPa=a[at_amplitude[-1]]["stress_MPa"],
            last_positive_peak_damage_degenv_MPa=b[at_amplitude[-1]]["stress_MPa"]))
    table(out,"DEGENV_EFFECT.csv",effects)
    virgin = []
    monotonic = np.linspace(0.,.3,1201).tolist()
    for path in PATHS:
        for option in OPTIONS:
            prefix,_ = rig(ops,path,OPTIONS[option],monotonic)
            for i in range(1,len(monotonic)):
                if prefix is None:
                    ops.setStrain(monotonic[i])
                else:
                    require(ops.analyze(1) == 0, "Virgin monotonic convergence failure")
                now = state(ops,prefix)
                expected = float(np.interp(now["strain"],[0.]+[v[1] for v in ENVELOPE],[0.]+[v[0] for v in ENVELOPE]))
                virgin.append(dict(path=path,option=option,step=i,strain=now["strain"],stress_MPa=now["stress_MPa"],
                                   expected_MPa=expected,error_MPa=abs(now["stress_MPa"]-expected)))
            ops.wipe()
    table(out,"VIRGIN_BACKBONE.csv",virgin)
    native_rows = [r for r in all_accepted if r["path"] != "RAW"]
    checks = {
        "degEnv_independent_effect_all_routes": all(r["independent_degenv_effect_pass"] for r in effects),
        "native_revert_all_routes": all(r["pass"] for r in reversions),
        "continued_stress_force_tangent_history_all_routes": all(r["pass_all"] for r in continuations),
        "virgin_frozen_backbone_all_routes": max(r["error_MPa"] for r in virgin) <= VIRGIN_TOL,
        "force_and_equilibrium_consistency": max(max(r["force_error_N"],r["equilibrium_error_N"]) for r in native_rows) <= FORCE_TOL,
        "native_work_consistency": max(r["energy_error_MPa"] for r in native_rows) <= ENERGY_TOL,
    }
    dump(out,"MATERIAL_RESULT.json", {"pass":all(checks.values()),"checks":checks,
        "virgin_max_error_MPa":max(r["error_MPa"] for r in virgin),
        "max_energy_error_MPa":max(r["energy_error_MPa"] for r in native_rows),
        "cyclic_paths":20,"monotonic_paths":8,"new_member_or_building_runs":0,
        "source_commit":SOURCE_SHA,"binary_sha256":sha(path_to_binary)})
    require(all(checks.values()), "MATERIAL BLOCK: inspect MATERIAL_RESULT.json and numerical comparisons")


def summarize(out, status):
    def optional(name):
        return load(out,name) if (out/name).exists() else {}
    imported = optional("BINARY_IMPORT.json")
    material = optional("MATERIAL_RESULT.json")
    failure = optional("FAILURE.json")
    result = {"binary_gate":"PASS" if imported.get("pass") else "BLOCK",
              "material_gate":"PASS" if material.get("pass") and not failure and status == "success" else "BLOCK",
              "material_test_executed":(out/"PREREGISTRATION.json").exists(),
              "material_test_completed":bool(material), "producer_run":RUN_ID,"source_commit":SOURCE_SHA,
              "binary_sha256":imported.get("binary_sha256"),"current_workflow_status":status,
              "failure":failure,"checks":material.get("checks",{}),
              "no_rebuild":True,"no_chao_or_structural_analysis":True,
              "commit_attestation_limit":"Build recipe/run provenance, not an embedded binary commit"}
    dump(out,"RESULT.json",result)
    issue = failure.get("message") or ("کنترل‌های عددی را در MATERIAL_RESULT.json ببینید." if material else "آزمون ماده تکمیل نشده؛ لاگ مرحله ناموفق را بررسی کنید.")
    report = (
        "# آزمون HystereticSM روی Artifact موجود\n\n"
        f"**BINARY: {result['binary_gate']} — MATERIAL: {result['material_gate']}**\n\n"
        f"اجرای سازنده: https://github.com/{REPO}/actions/runs/{RUN_ID}\n\n"
        f"Commit سورس: `{SOURCE_SHA}`\n\n"
        f"SHA256 باینری: `{result['binary_sha256']}`\n\n"
        "هیچ ساخت مجدد، تحلیل Chao یا ساختمان و هیچ تغییر پارامتر فیزیکی انجام نشد. تاریخچه، Backbone، گام و تلورانس‌های Gate قبلی ثابت‌اند. "
        "آزمون شامل ماده خام، المان مستقیم بدون Fiber و Fiber با/بدون Aggregator است. آزمون ماده خام API صریح revert ندارد؛ "
        "بازگشت واقعی Domain در سه مسیر المانی با یک و سه تکرار گام ردشده کنترل می‌شود.\n\n"
        "مقادیر مشاهده‌پذیر کرنش، تنش، tangent، نیرو، انرژی، Envelope و تاریخچه AllData مقایسه می‌شوند. "
        "CdamfcPeak خصوصی و FI مستقیماً قابل ثبت نیستند؛ شاخص ساختگی معرفی نشده است.\n\n"
        "منشأ Commit با Run، Workflow ثابت، Artifact و checksum سازنده احراز می‌شود؛ باینری Commit تعبیه‌شده یا attestation امضاشده ندارد. "
        "موفقیت ساخت قبلی به معنی قبولی ماده نیست.\n\n"
    )
    for name,passed in result["checks"].items():
        report += f"- {name}: {'PASS' if passed else 'BLOCK'}\n"
    report += "\n" + ("Gate قابلیت محدود ماده قبول شد؛ این نتیجه اعتبارسنجی تجربی Chao یا تأیید forceBeamColumn عضو نیست." if result["material_gate"] == "PASS" else "علت/محل پیگیری: " + issue) + "\n"
    (out/"REPORT_FA.md").write_text(report,encoding="utf-8")
    table(out,"OUTPUT_SHA256.csv",[{"file":str(p.relative_to(out)),"sha256":sha(p)} for p in sorted(out.rglob("*")) if p.is_file() and p.name != "OUTPUT_SHA256.csv"])
    print(json.dumps(result,indent=2,ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=("audit-run","verify-binary","run","summarize"))
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--binary-root",type=Path)
    parser.add_argument("--job-status",default="success")
    args = parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    try:
        if args.mode == "audit-run":
            audit_run(args.out)
        elif args.mode == "verify-binary":
            require(args.binary_root is not None,"--binary-root required")
            verify_binary(args.binary_root,args.out)
        elif args.mode == "run":
            require(args.binary_root is not None,"--binary-root required")
            run_gate(args.binary_root,args.out)
        else:
            summarize(args.out,args.job_status)
    except Exception as error:
        dump(args.out,"FAILURE.json",{"mode":args.mode,"message":str(error),"traceback":traceback.format_exc()})
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
