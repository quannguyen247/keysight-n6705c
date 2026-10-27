# Keysight N6705C power captures of PQC test loops on a Lichee RV Nano W

Board-input voltage and current traces recorded while a Lichee RV Nano W (RV64) ran the ML-KEM, ML-DSA, and SLH-DSA test loops used in the paper *An Adaptive Post-Quantum Cryptography Architecture for IoT: Deployment and Evaluation for Quantum-Safe Networks* (Dong-Quan Nguyen, Quan Le, Nhat-Phat Huynh, University of Information Technology, VNU-HCM).

The repository holds the raw logger files, the operator's run notes, the script that produces the paper's energy table, and its output. Everything in `analysis/` can be regenerated from `data/` with the script.

## Contents

```
data/       12 raw .dlog files, one per parameter set, and the operator's run notes
scripts/    analyse_energy_logs.py (Python standard library only)
analysis/   summary.json, energy_results.csv, decoder_crosscheck.json
```

## Measurement setup

- Board: Lichee RV Nano W with a XuanTie C906-class RV64 core at a fixed 1.0 GHz, custom Ubuntu 22.04 image, Linux 5.10.4.
- Instrument: Keysight N6705C mainframe with an N6781A source/measure unit on channel 1. Channel 2 was not logged.
- Supply: 5.2 V from channel 1, because the board did not boot at 5.0 V. Two leads go to the board input and sensing is local, so voltage and power are measured at the instrument terminals and include the lead losses.
- Logger: Data Logger mode, voltage and current with autoranging, logging interval 0.49152 ms (`tint` = 0.00049151998 s), min/max off. Each stored value is the mean over one interval.
- Start: the operator pressed Enter on the host and Run on the instrument at the same time. The offset between the two is estimated at a few hundred milliseconds.
- Date: 26 October 2026, from 06:38 to 10:30 on the instrument clock.

## Workloads

Each capture covers one single-process run of a test program from the research forks below, built on the board with gcc and the forks' `-O3` Makefiles, with `NTESTS` set to the count in the notes.

| Family | Program | Source (tag `paper-v1.0`) | One iteration |
|---|---|---|---|
| ML-KEM | `test_kyber` | [kyber-dev](https://github.com/quannguyen247/kyber-dev) | key generation, encapsulation, decapsulation |
| ML-DSA | `test_dilithium` | [dilithium-dev](https://github.com/quannguyen247/dilithium-dev) | key generation, signing, verification |
| SLH-DSA | `test_sphincsplus` | [sphincsplus-dev](https://github.com/quannguyen247/sphincsplus-dev) | key generation, signing, verification |

`data/Ntest_and_duration.txt` gives `NTESTS` for each run and two times written as `run/capture`: when the operator saw the run finish, and the length set on the logger. The closing lines of the note are in Vietnamese and say the same thing.

## Reproducing the numbers

Python 3.8 or newer, no extra packages:

```sh
python3 scripts/analyse_energy_logs.py --tails
python3 scripts/analyse_energy_logs.py --output analysis/summary.json --csv analysis/energy_results.csv
```

The first command prints the LaTeX rows of the paper's energy table and, for each run, the noted completion time next to the edge found in the trace. `--check-table path/to/table.tex` exits with an error if any of the twelve rounded rows is missing from a LaTeX table.

## Method

1. Decode. Each file has an SGML header, two big-endian 32-bit integers (start index and pair count), then big-endian float32 voltage/current pairs. The script checks the pair count against the payload size and rejects non-finite values.
2. Find the end of the run. The active level is the median power of 0.25 s blocks from 1 s after the start to 2 s before the noted completion; the post-run level is the median of the last 1.5 s. The run ends at the first 50 ms block below the midpoint of the two levels, provided the following 0.5 s also stays below it. When the two levels differ by no more than 0.05 W, the program was still running at the end and the whole capture is used.
3. Integrate. Window energy is the sum of V × I × Δt over the stored intervals from logger start to the edge.
4. Estimate idle power as the mean from 0.25 s after the edge to the end of the capture. ML-KEM-512 and ML-DSA-44 have no tail and use the idle level of the capture taken about five minutes earlier (ML-KEM-768 and ML-DSA-65).
5. Incremental energy per iteration is (window energy − idle power × window length) / `NTESTS`, rounded to two significant figures.

## Results

| Configuration | NTESTS | Window (s) | Mean power (W) | Idle power (W) | Max. stored current (mA) | Window energy (J) | Incremental energy per iteration (mJ) |
|---|---:|---:|---:|---:|---:|---:|---:|
| ML-KEM-512 † | 15,000 | 30.0 | 0.724 | 0.617 * | 366.1 | 21.7 | ≥ 0.21 |
| ML-KEM-768 | 8,000 | 27.0 | 0.728 | 0.617 | 307.5 | 19.6 | 0.37 |
| ML-KEM-1024 | 5,000 | 25.1 | 0.718 | 0.605 | 299.1 | 18.0 | 0.57 |
| ML-DSA-44 † | 3,000 | 30.0 | 0.733 | 0.611 * | 281.7 | 22.0 | ≥ 1.2 |
| ML-DSA-65 | 1,500 | 24.9 | 0.735 | 0.611 | 331.5 | 18.3 | 2.0 |
| ML-DSA-87 | 1,200 | 27.7 | 0.732 | 0.608 | 277.6 | 20.2 | 2.9 |
| SLH-DSA-SHA2-128s | 3 | 38.2 | 0.706 | 0.610 | 279.9 | 27.0 | 1,200 |
| SLH-DSA-SHA2-192s | 3 | 80.8 | 0.710 | 0.608 | 298.5 | 57.3 | 2,700 |
| SLH-DSA-SHA2-256s | 3 | 75.4 | 0.736 | 0.634 | 297.2 | 55.5 | 2,600 |
| SLH-DSA-SHAKE-128s | 3 | 83.6 | 0.777 | 0.640 | 293.9 | 64.9 | 3,800 |
| SLH-DSA-SHAKE-192s | 3 | 142.1 | 0.770 | 0.635 | 313.0 | 109.4 | 6,400 |
| SLH-DSA-SHAKE-256s | 3 | 121.1 | 0.767 | 0.636 | 345.8 | 92.9 | 5,300 |

† Still running when the 30 s capture ended, so the window holds fewer than `NTESTS` iterations and the last column is a lower bound.
\* Idle power borrowed from the capture taken about five minutes earlier.

The test loops add 0.097 to 0.136 W on top of an idle level of 0.605 to 0.640 W, so most of the window energy is the board's own idle draw. The idle level rose by about 0.03 W between the 09:48 and 09:55 captures; keep that in mind when comparing mean power across rows.

## Noted completion times against the trace

| Configuration | Time (instrument clock) | Noted (s) | Edge in trace (s) | Tail used (s) | Tail power (W) |
|---|---|---:|---:|---:|---:|
| ML-DSA-87 | 06:38 | 28 | 27.65 | 2.10 | 0.608 |
| ML-KEM-1024 | 06:44 | 28 | 25.12 | 4.63 | 0.605 |
| ML-DSA-65 | 08:20 | 25 | 24.87 | 4.88 | 0.611 |
| ML-DSA-44 | 08:25 | 29 | none, active until 30 s | – | – |
| ML-KEM-768 | 08:33 | 27 | 26.96 | 2.79 | 0.617 |
| ML-KEM-512 | 08:39 | 30 | none, active until 30 s | – | – |
| SLH-DSA-SHA2-128s | 09:14 | 38 | 38.23 | 6.52 | 0.610 |
| SLH-DSA-SHA2-192s | 09:48 | 80 | 80.77 | 8.98 | 0.608 |
| SLH-DSA-SHA2-256s | 09:55 | 76 | 75.41 | 4.34 | 0.634 |
| SLH-DSA-SHAKE-128s | 10:18 | 83 | 83.60 | 1.15 | 0.640 |
| SLH-DSA-SHAKE-192s | 10:24 | 143 | 142.08 | 2.67 | 0.635 |
| SLH-DSA-SHAKE-256s | 10:30 | 120 | 121.13 | 0.62 | 0.636 |

Nine of the ten detected edges fall within about 1 s of the note. ML-KEM-1024 finished near 25.1 s, almost 3 s before the noted 28 s, which is why the analysis uses the trace and not the note.

## Limitations

- One capture per configuration, so there is no between-run spread.
- There is no separate idle capture. Idle power comes from post-run tails of 0.6 to 9.0 s, and the tails contain background bursts of about 0.03 W every few seconds.
- ML-KEM-512 and ML-DSA-44 outlast their 30 s captures.
- The start is manual and there are no hardware markers. If the program started a few hundred milliseconds before the logger, a 25 to 30 s window misses about 1% of its iterations.
- Each iteration mixes three operations, so the data give no energy per primitive.
- Calibration status, the current limit, firmware version, CPU temperature, and the exact command lines were not recorded.
- Energy uses the product of interval-mean voltage and current. Interval means stay between 5.192 and 5.208 V, but the files cannot bound the within-interval covariance.

## Decoder check

`analysis/decoder_crosscheck.json` records a comparison with the independent reader in [derf/dlog-viewer](https://github.com/derf/dlog-viewer): all twelve files decode to the same pair counts and identical voltage/current values.

## Citation

Citation details are in `CITATION.cff`. Please cite the paper if you use these captures.
