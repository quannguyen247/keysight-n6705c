"""Compare per-iteration times from the captures with pqm4 cycle counts and stress-test CPU time.

Needs analysis/summary.json (run analyse_energy_logs.py --output analysis/summary.json first).
Standard library only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "analysis" / "summary.json"
CLOCK_HZ = 1.0e9  # fixed CPU frequency of the board image

# Mean cycles of the pqm4 "clean" implementations on a Cortex-M4 (keygen, encaps or sign, decaps or verify),
# from https://github.com/mupq/pqm4/blob/b1a9fd1ffcbb9dfd683083f2062725528ed907b1/benchmarks.md
PQM4 = {
    "ML-KEM-512": (595_793, 700_605, 888_653),
    "ML-KEM-768": (988_722, 1_138_225, 1_387_984),
    "ML-KEM-1024": (1_536_343, 1_708_071, 2_020_327),
    "ML-DSA-44": (1_874_405, 7_925_955, 2_063_096),
    "ML-DSA-65": (3_205_533, 12_359_056, 3_377_305),
    "ML-DSA-87": (5_341_863, 15_579_513, 5_610_203),
    "SLH-DSA-SHA2-128s": (1_007_731_522, 7_657_558_168, 7_471_794),
    "SLH-DSA-SHA2-192s": (1_509_654_951, 15_452_089_990, 13_494_855),
    "SLH-DSA-SHA2-256s": (1_001_040_810, 14_326_202_444, 19_637_153),
    "SLH-DSA-SHAKE-128s": (3_231_401_965, 24_553_696_412, 24_366_771),
    "SLH-DSA-SHAKE-192s": (4_793_551_013, 43_114_327_277, 35_026_412),
    "SLH-DSA-SHAKE-256s": (3_201_898_694, 38_175_697_620, 52_912_174),
}

# Per-client stress-test means from the paper's stress table (elapsed, user CPU, system CPU in ms;
# concurrent sessions per batch). Stress clients decapsulate (ML-KEM) or sign (ML-DSA, SLH-DSA) once.
STRESS = {
    "ML-KEM-512": (1783.63, 1.83, 0.74, 80),
    "ML-KEM-768": (2039.45, 2.29, 0.77, 80),
    "ML-KEM-1024": (1926.38, 2.87, 0.98, 80),
    "ML-DSA-44": (1047.11, 7.22, 0.90, 80),
    "ML-DSA-65": (1419.34, 11.50, 1.13, 80),
    "ML-DSA-87": (1446.34, 14.00, 1.36, 80),
    "SLH-DSA-SHA2-128s": (135925.17, 11416.68, 9.92, 10),
    "SLH-DSA-SHA2-192s": (308673.02, 26123.06, 31.03, 10),
    "SLH-DSA-SHA2-256s": (296101.86, 24835.22, 32.71, 10),
    "SLH-DSA-SHAKE-128s": (296865.17, 24937.65, 21.49, 10),
    "SLH-DSA-SHAKE-192s": (518074.23, 43346.48, 38.70, 10),
    "SLH-DSA-SHAKE-256s": (450439.57, 37764.38, 34.10, 10),
}


def compare(capture: dict) -> dict:
    name = capture["parameter"]
    keygen, middle, last = PQM4[name]
    iteration_s = capture["integrated_duration_s"] / capture["ntest_reported"]
    stress_op = last if name.startswith("ML-KEM") else middle  # decapsulation or signing
    predicted_ms = 1000 * iteration_s * stress_op / (keygen + middle + last)
    elapsed, user, system, sessions = STRESS[name]
    return {
        "parameter": name, "completion_captured": capture["completion_captured"],
        "rv64_mcycles": iteration_s * CLOCK_HZ / 1e6, "m4_mcycles": (keygen + middle + last) / 1e6,
        "rv64_over_m4": iteration_s * CLOCK_HZ / (keygen + middle + last),
        "predicted_stress_op_ms": predicted_ms, "stress_user_ms": user,
        "stress_over_predicted": user / predicted_ms,
        "batch_cpu_over_elapsed": sessions * (user + system) / elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Save the comparison as JSON")
    args = parser.parse_args()
    captures = json.loads(SUMMARY.read_text(encoding="utf-8"))["captures"]
    rows = [compare(c) for c in captures]
    for r in rows:
        bound = "" if r["completion_captured"] else " (run outlasts capture: bound)"
        print(f"{r['parameter']}: RV64/M4 {r['rv64_over_m4']:.2f}, stress CPU minus prediction {r['excess_ms']:.2f} ms "
              f"({100 * r['excess_rel']:.1f}%), batch CPU/elapsed {r['batch_cpu_over_elapsed']:.3f}{bound}")
    if args.output:
        args.output.write_text(json.dumps({"pqm4_commit": "b1a9fd1", "clock_hz": CLOCK_HZ, "rows": rows}, indent=2) + "\n",
                               encoding="utf-8")


if __name__ == "__main__":
    main()
