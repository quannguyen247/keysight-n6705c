"""Recalculate the N6705C test-loop captures using Python's standard library.

The DLOG layout was checked against derf/dlog-viewer (DLog.load_dlog).
No upstream code is embedded. Input captures and the operator note are read only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import struct
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
NOTE = DATA / "Ntest_and_duration.txt"
ORDER = [
    "kyber512", "kyber768", "kyber1024",
    "dilithium2", "dilithium3", "dilithium5",
    "sha2-128s", "sha2-192s", "sha2-256s",
    "shake-128s", "shake-192s", "shake-256s",
]
LABELS = dict(zip(ORDER, [
    "ML-KEM-512", "ML-KEM-768", "ML-KEM-1024",
    "ML-DSA-44", "ML-DSA-65", "ML-DSA-87",
    "SLH-DSA-SHA2-128s", "SLH-DSA-SHA2-192s", "SLH-DSA-SHA2-256s",
    "SLH-DSA-SHAKE-128s", "SLH-DSA-SHAKE-192s", "SLH-DSA-SHAKE-256s",
]))
EDGE_BLOCK_S = 0.05    # resolution of the falling-edge search
LEVEL_BLOCK_S = 0.25   # block length for the active/tail level medians
TAIL_LEVEL_S = 1.5     # final stretch whose median defines the post-run level
MIN_STEP_W = 0.05      # smaller active-to-tail steps mean the program was still running
SEARCH_BEFORE_NOTE_S = 5.0  # the edge search starts this long before the noted completion
EDGE_HOLD_S = 0.5      # the drop must persist for this long
TAIL_SETTLE_S = 0.25   # skipped after the edge before averaging the tail
MIN_TAIL_S = 0.5       # shorter tails are not used as idle estimates
DAGGER, ASTERISK, GEQ = r"$^{\dagger}$", r"$^{\ast}$", r"$\geq$"
SENS_STEP_FRACTIONS = (0.3, 0.5, 0.7)  # edge threshold as a fraction of the active-to-tail step
SENS_HOLD_S = (0.25, 0.5, 1.0)
SENS_SETTLE_S = (0.1, 0.25, 0.5)
SEGMENTS = 10           # equal segments of [1 s, end - 1 s] for the within-run spread
BASELINE_STEP = "09:50"  # idle rose by about 0.03 W between the 09:48 and 09:55 captures
NOTE_LINE = re.compile(
    r"^\+\s+(?:sphincsplus\s+)?(?P<name>\S+)\s+ntest\s*=\s*(?P<count>\d+)"
    r"\s+(?:durations:\s*)?(?P<run>[\d.]+)/(?P<capture>[\d.]+)s$"
)


def read_notes() -> dict:
    notes = {}
    for line in NOTE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("+"):
            continue
        match = NOTE_LINE.fullmatch(line.strip())
        if match is None:
            raise ValueError(f"Unrecognized annotation: {line!r}")
        name = match["name"]
        if name in notes:
            raise ValueError(f"Duplicate annotation: {name}")
        notes[name] = (int(match["count"]), float(match["run"]), float(match["capture"]))
    if set(notes) != set(ORDER):
        raise ValueError("Expected annotations for all twelve parameter sets")
    return notes


def decode(path: Path) -> tuple[ET.Element, list[tuple[float, float]], int]:
    content = path.read_bytes()
    end = content.index(b"</dlog>\n") + len(b"</dlog>\n")
    # The instrument's SGML-like option names (e.g. 1ua) are not legal XML names.
    header = re.sub(rb"<(/?)([0-9][A-Za-z0-9_]*)", rb"<\1option_\2", content[:end])
    root = ET.fromstring(header)
    channels = root.findall("channel")
    enabled = [(c.get("id"), c.findtext("sense_volt"), c.findtext("sense_curr"))
               for c in channels if c.findtext("sense_volt") == "1" or c.findtext("sense_curr") == "1"]
    if enabled != [("1", "1", "1")] or root.findtext("frame/sense_minmax") != "0":
        raise ValueError(f"Unsupported channel/minmax layout: {path.name}")
    start, count = struct.unpack_from(">II", content, end)
    payload = content[end + 8:]
    if start != 0 or len(payload) != count * 8:
        raise ValueError(f"Incomplete or unsupported binary frame: {path.name}")
    pairs = list(struct.iter_unpack(">ff", payload))
    if not pairs or not all(math.isfinite(v) and math.isfinite(i) for v, i in pairs):
        raise ValueError(f"Empty or non-finite capture: {path.name}")
    return root, pairs, end + 8


def block_means(power: list[float], dt: float, width: float,
                t0: float = 0.0, t1: float | None = None) -> tuple[list[float], float]:
    step = int(width / dt)
    stop = len(power) if t1 is None else min(len(power), int(t1 / dt))
    means = [math.fsum(power[k:k + step]) / step for k in range(int(t0 / dt), stop - step + 1, step)]
    return means, step * dt


def find_completion(power: list[float], dt: float, noted_run: float,
                    step_fraction: float = 0.5, hold_s: float = EDGE_HOLD_S) -> float | None:
    """Return the first sustained drop from the active level to the post-run level, if captured."""
    coverage = len(power) * dt
    run = min(noted_run, coverage)
    active = statistics.median(block_means(power, dt, LEVEL_BLOCK_S, 1.0, run - 2.0)[0])
    tail = statistics.median(block_means(power, dt, LEVEL_BLOCK_S, coverage - TAIL_LEVEL_S)[0])
    if active - tail <= MIN_STEP_W:
        return None
    threshold = tail + step_fraction * (active - tail)
    fine, width = block_means(power, dt, EDGE_BLOCK_S)
    hold = round(hold_s / width)
    for k in range(int(max(0.0, run - SEARCH_BEFORE_NOTE_S) / width), len(fine) - hold + 1):
        if fine[k] < threshold and math.fsum(fine[k:k + hold]) / hold < threshold:
            return k * width
    return None


def analyse(name: str, note: tuple[int, float, float]) -> dict:
    path = DATA / f"{name}.dlog"
    root, pairs, offset = decode(path)
    ntest, noted_run, noted_capture = note
    dt = float(root.findtext("frame/tint"))
    planned = float(root.findtext("frame/time"))
    coverage = len(pairs) * dt
    if dt <= 0 or noted_run <= 0 or abs(coverage - planned) > dt:
        raise ValueError(f"Invalid sample period or incomplete duration: {name}")
    trace_power = [v * i for v, i in pairs]
    edge = find_completion(trace_power, dt, noted_run)
    duration = coverage if edge is None else edge
    tail_idle, tail_used = None, None
    if edge is not None and coverage - edge - TAIL_SETTLE_S >= MIN_TAIL_S:
        first = int((edge + TAIL_SETTLE_S) / dt)
        tail_idle = math.fsum(trace_power[first:]) / (len(trace_power) - first)
        tail_used = coverage - edge - TAIL_SETTLE_S
    full = min(int(duration / dt), len(pairs))
    selected = pairs[:full]
    weights = [dt] * full
    remainder = duration - full * dt
    if remainder > 1e-12 and full < len(pairs):
        selected.append(pairs[full])
        weights.append(remainder)
    voltage = [p[0] for p in selected]
    current = [p[1] for p in selected]
    power = [v * i for v, i in selected]
    energy = math.fsum(p * w for p, w in zip(power, weights))
    vmean = math.fsum(v * w for v, w in zip(voltage, weights)) / duration
    imean = math.fsum(i * w for i, w in zip(current, weights)) / duration
    pmean = energy / duration
    return {
        "file": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "parameter": LABELS[name], "ntest_reported": ntest, "captures": 1,
        "instrument_date": root.findtext("frame/date").strip('"'),
        "module": root.findtext("channel/ident/model"),
        "voltage_autorange": root.findtext("channel/volt_auto_range") == "1",
        "current_autorange": root.findtext("channel/curr_auto_range") == "1",
        "minmax_enabled": False, "trigger_source_code": root.findtext("frame/trig_source"),
        "trigger_offset": float(root.findtext("frame/offset")),
        "data_offset_bytes": offset, "stored_pairs": len(pairs), "sample_period_s": dt,
        "header_duration_s": planned, "note_capture_duration_s": noted_capture,
        "stored_coverage_s": coverage, "note_active_duration_s": noted_run,
        "trace_edge_s": edge, "completion_captured": edge is not None,
        "tail_used_s": tail_used, "tail_mean_power_W": tail_idle,
        "integrated_duration_s": duration, "used_pairs": len(selected),
        "last_interval_weight_s": weights[-1], "excluded_tail_s": coverage - duration,
        "voltage_mean_V": vmean, "voltage_min_V": min(voltage), "voltage_max_V": max(voltage),
        "current_mean_A": imean, "current_min_A": min(current), "current_max_A": max(current),
        "power_mean_W": pmean, "power_min_W": min(power), "power_max_W": max(power),
        "estimated_window_energy_J": energy,
        "product_of_means_relative_difference": abs(vmean * imean - pmean) / pmean,
    }


def started(row: dict) -> datetime:
    return datetime.strptime(row["instrument_date"], "%a %b %d %H:%M:%S %Y")


def with_idle(row: dict, rows: list[dict]) -> dict:
    """Subtract the capture's own tail idle, or that of the nearest capture in time with a usable tail."""
    source = row if row["tail_mean_power_W"] is not None else min(
        (r for r in rows if r["tail_mean_power_W"] is not None),
        key=lambda r: abs((started(r) - started(row)).total_seconds()))
    idle = source["tail_mean_power_W"]
    incremental = row["estimated_window_energy_J"] - idle * row["integrated_duration_s"]
    return {
        **row, "idle_power_W": idle, "idle_source": source["parameter"],
        "incremental_energy_J": incremental,
        "incremental_energy_per_iteration_mJ": 1000 * incremental / row["ntest_reported"],
    }


def two_significant(value: float) -> str:
    """Round to two significant figures, matching the idle-subtraction uncertainty."""
    decimals = 1 - math.floor(math.log10(abs(value)))
    return f"{round(value, decimals):,.{max(decimals, 0)}f}"


def uncertainty(rows: list[dict], notes: dict) -> list[dict]:
    """Parameter sensitivity, within-run segment spread, and same-session idle spread of e_iter."""
    own = [r for r in rows if r["tail_mean_power_W"] is not None]
    spreads = [statistics.stdev([r["tail_mean_power_W"] for r in own if (started(r).strftime("%H:%M") < BASELINE_STEP) == early])
               for early in (True, False)]
    idle_u = max(spreads)
    by_label = {LABELS[n]: n for n in ORDER}
    out = []
    for r in rows:
        name = by_label[r["parameter"]]
        root, pairs, _ = decode(DATA / f"{name}.dlog")
        dt = float(root.findtext("frame/tint"))
        power = [v * i for v, i in pairs]
        coverage = len(power) * dt
        nominal = r["incremental_energy_per_iteration_mJ"]
        variants = []
        for fraction in SENS_STEP_FRACTIONS:
            for hold in SENS_HOLD_S:
                edge = find_completion(power, dt, notes[name][1], fraction, hold)
                window = coverage if edge is None else edge
                energy = math.fsum(power[:int(window / dt)]) * dt
                for settle in SENS_SETTLE_S:
                    idle = r["idle_power_W"]
                    if edge is not None and coverage - edge - settle >= MIN_TAIL_S:
                        first = int((edge + settle) / dt)
                        idle = math.fsum(power[first:]) / (len(power) - first)
                    variants.append(1000 * (energy - idle * window) / r["ntest_reported"])
        increment = r["power_mean_W"] - r["idle_power_W"]
        length = (r["integrated_duration_s"] - 2.0) / SEGMENTS
        bounds = [int((1.0 + j * length) / dt) for j in range(SEGMENTS + 1)]
        segments = [math.fsum(power[a:b]) / (b - a) for a, b in zip(bounds, bounds[1:])]
        segment_rel = statistics.stdev(segments) / math.sqrt(SEGMENTS) / increment
        idle_rel = idle_u / increment
        out.append({
            "parameter": r["parameter"],
            "sensitivity_max_rel": max(abs(v - nominal) for v in variants) / nominal,
            "segment_se_rel": segment_rel, "idle_spread_W": idle_u, "idle_rel": idle_rel,
            "combined_standard_rel": math.hypot(segment_rel, idle_rel),
        })
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = ["parameter", "ntest_reported", "completion_captured", "integrated_duration_s", "power_mean_W",
              "idle_power_W", "idle_source", "current_max_A", "estimated_window_energy_J",
              "incremental_energy_per_iteration_mJ"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def latex_rows(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        open_run = not r["completion_captured"]
        lines.append(
            f"{r['parameter']}{DAGGER if open_run else ''} & {r['ntest_reported']:,} & "
            f"{r['integrated_duration_s']:.1f} & {r['power_mean_W']:.3f} & "
            f"{r['idle_power_W']:.3f}{ASTERISK if r['idle_source'] != r['parameter'] else ''} & "
            f"{1000 * r['current_max_A']:.1f} & {r['estimated_window_energy_J']:.1f} & "
            f"{GEQ if open_run else ''}{two_significant(r['incremental_energy_per_iteration_mJ'])} \\\\"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Save the complete derived JSON report")
    parser.add_argument("--csv", type=Path, help="Save one unrounded row per capture as CSV")
    parser.add_argument("--check-table", type=Path, metavar="TEX",
                        help="Check that a LaTeX table contains the twelve rounded rows")
    parser.add_argument("--tails", action="store_true", help="Compare annotated completion with the trace edge and tail power")
    parser.add_argument("--uncertainty", action="store_true",
                        help="Print parameter sensitivity and the standard uncertainty of each e_iter (slow)")
    args = parser.parse_args()
    notes = read_notes()
    if {p.stem for p in DATA.glob("*.dlog")} != set(ORDER):
        raise ValueError("Expected exactly the twelve documented captures")
    rows = [analyse(n, notes[n]) for n in ORDER]
    rows = [with_idle(r, rows) for r in rows]
    report = {
        "note_sha256": hashlib.sha256(NOTE.read_bytes()).hexdigest(),
        "method": "Sum products of paired stored interval means over [0, falling power edge), or the whole capture "
                  "when the program is still active at its end; subtract the post-run tail mean power, borrowed from "
                  "the nearest capture in time when no tail exists, and divide by Ntest.",
        "limits": ["No independent repeated captures or separate idle capture; idle comes from short post-run tails.",
                   "No intra-interval V/I covariance correction, calibration budget or hardware-aligned workload markers.",
                   "Ntest is the NTESTS loop count of test_kyber/test_dilithium/test_sphincsplus (operator-confirmed); "
                   "the exact invocation and successful-operation counts are not recorded.",
                   "Current maxima are maxima of stored interval means, not transient peaks."],
        "captures": rows,
    }
    if args.uncertainty or args.output:
        report["uncertainty"] = uncertainty(rows, notes)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.csv:
        write_csv(args.csv, rows)
    rendered = latex_rows(rows)
    print(rendered)
    if args.tails:
        for r in rows:
            edge = "none" if r["trace_edge_s"] is None else f"{r['trace_edge_s']:.2f}"
            tail = "-" if r["tail_mean_power_W"] is None else f"{r['tail_mean_power_W']:.3f} W over {r['tail_used_s']:.2f} s"
            print(f"{r['parameter']}: note {r['note_active_duration_s']:.0f} s, trace edge {edge} s, tail {tail}, "
                  f"idle {r['idle_power_W']:.3f} W from {r['idle_source']}")
    if args.uncertainty:
        for u in report["uncertainty"]:
            print(f"{u['parameter']}: parameter sensitivity {100 * u['sensitivity_max_rel']:.1f}%, "
                  f"segment SE {100 * u['segment_se_rel']:.1f}%, idle {100 * u['idle_rel']:.1f}%, "
                  f"combined {100 * u['combined_standard_rel']:.1f}%")
    if args.check_table:
        table = args.check_table.read_text(encoding="utf-8")
        for row in rendered.splitlines():
            if row not in table:
                raise ValueError(f"Table row differs from the capture: {row}")
        print(f"PASS: {args.check_table} contains all twelve rounded rows.")


if __name__ == "__main__":
    main()
