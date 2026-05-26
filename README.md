# ZAP

**ZAP** (Zoned Architecture and Performant compiler) is a research prototype for compiling quantum circuits onto a **field-programmable neutral-atom array** model: storage zones, entanglement zones, AOD-mediated movement, and staged Rydberg-style two-qubit execution.

[ZAP: Zoned Architecture and Performant Compiler for Field Programmable Atom Array](https://ieeexplore.ieee.org/document/11535023)

Cite This: C. Huang et al., "ZAP: Zoned Architecture and Performant Compiler for Field Programmable Atom Array," in IEEE Transactions on Quantum Engineering, doi: 10.1109/TQE.2026.3696707. 

## Features

- **Scheduling**: ASAP schedules over a linearized gate list (OpenQASM 2/3 or JSON graph benchmarks).
- **Placement & routing**: Greedy placement within a Rydberg-radius constraint; parallel-friendly move batches; optional idle-qubit policies (crosstalk vs. return-to-storage).
- **Simulation**: Multiplicative fidelity model (gates, transfer, idle/crosstalk, global coherence).
- **Visualization**: MP4 animation of the compiled instruction trace (requires `ffmpeg` on `PATH`).

## Repository layout

```text
Neutral_Atom_Compilation/
├── run.py                  # CLI entry point
├── requirements.txt
├── LICENSE
├── setting/                # Experiment presets (paths to benchmarks, flags, output name)
├── architecture/         # Hardware JSON (durations, fidelities, zones, routing knobs)
├── benchmark/            # Input circuits (QASM, JSON)
├── zap/
│   ├── zap.py              # End-to-end pipeline
│   ├── scheduler/          # Gate scheduling
│   ├── placer/             # Per-stage placement
│   ├── router/             # Move planning + instruction emission
│   ├── simulator/          # Fidelity / duration roll-up
│   └── animator/           # Matplotlib + ffmpeg animation
└── results/                # Generated code, logs, animations (created at runtime)
```

## Requirements

- Python 3.10+ recommended (tested with 3.13 in development).
- [ffmpeg](https://ffmpeg.org/) installed and discoverable as `ffmpeg` when `animation` is enabled.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Quick start

Run with a setting file under `setting/` (omit the `.json` suffix):

```bash
python run.py default
python run.py tqe
python run.py graphs
```

`python run.py default` loads `setting/default.json`. Paths inside the setting file are relative to the `benchmark/` directory.

## CLI overrides

```bash
python run.py <setting_name> \
  --scheduling_strategy asap_separate \
  --placement_strategy baseline \
  --routing_strategy baseline
```

### Scheduling (`--scheduling_strategy`)

| Value           | Description                                                                              |
| --------------- | ---------------------------------------------------------------------------------------- |
| `asap_separate` | Schedule all two-qubit layers first (ASAP), then insert single-qubit gates between them. |
| `asap_joint`    | Single ASAP schedule over all gates in circuit order.                                    |

### Routing / idle policy (`--routing_strategy`)

The placer uses this name to choose how **idle** qubits in the entanglement zone are handled during global two-qubit illumination:

| Value         | Description                                                                                    |
| ------------- | ---------------------------------------------------------------------------------------------- |
| `baseline`    | Compare estimated crosstalk vs. move+decoherence; pick the cheaper option (`lookahead`-style). |
| `lookahead`   | Same cost heuristic as `baseline`.                                                             |
| `always_move` | Prefer moving idles to storage when possible.                                                  |
| `always_stay` | Do not evict idles from the entanglement zone for this reason.                                 |

Other routing labels accepted by the CLI may not be fully implemented in this tree; unsupported combinations can raise at runtime.

### Placement (`--placement_strategy`)

The CLI accepts several strategy names for compatibility with the paper and experiments. In this repository snapshot, extended **layered** placement/routing hooks are not implemented: if you select a layered strategy, the compiler **emits a warning** and continues with the **standard** greedy router/placer path.

## Setting file format

Example:

```json
{
  "benchmark": ["graphs/graphs_10_0.qasm"],
  "architecture": "default.json",
  "simulation": true,
  "animation": true,
  "output_dir": "default"
}
```

| Field                        | Meaning                                                              |
| ---------------------------- | -------------------------------------------------------------------- |
| `benchmark`                  | List of paths under `benchmark/`                                     |
| `architecture`               | Filename under `architecture/`                                       |
| `simulation`                 | Run fidelity/duration simulation after routing                       |
| `animation`                  | Write `results/<output_dir>/animations/<name>.mp4`                   |
| `output_dir`                 | Subdirectory under `results/`                                        |
| `initial_mapping` (optional) | Explicit qubit index → site list; empty list means automatic mapping |
| `routing_cfg` (optional)     | Shallow merge into `architecture["routing"]` for one-off experiments |

## Architecture JSON (routing)

Example fragment:

```json
{
  "routing": {
    "parking_dist": 1,
    "parallel_priority_weight": 1000.0,
    "initial_mapping_parallel_lookahead": 3,
    "idle_cost_alpha": 1.0
  }
}
```

- `parallel_priority_weight`: Penalizes move vectors that would conflict under the same 2D compatibility rules used when batching AOD moves.
- `initial_mapping_parallel_lookahead`: Number of early stages used when seeding storage→entanglement assignments for parallel first moves.
- `idle_cost_alpha`: Scales the decoherence term in the idle-vs-move heuristic.

## Outputs

For each benchmark entry:

- `results/<output_dir>/code/<benchmark_stem>_code.json` — instruction trace (init, activate/deactivate, moves, gates, crosstalk markers).
- `results/<output_dir>/log/<benchmark>.json` — appended simulation records when `simulation` is true.
- `results/<output_dir>/animations/<benchmark>.mp4` — when `animation` is true.

## Citation

If you use this code or the ZAP idea in research, please cite the paper linked at the top of this README.

## License

This project is released under the MIT License — see [`LICENSE`](LICENSE).
