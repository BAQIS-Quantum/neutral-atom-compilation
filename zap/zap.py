import json
import os
import time
from datetime import datetime
from qiskit import transpile, QuantumCircuit
from qiskit_qasm3_import import parse

from zap.scheduler.scheduler import Scheduler
from zap.router.router import Router
from zap.simulator.simulator import Simulator
from zap.animator.animator import Animator


class Zap:
    """Compile a circuit benchmark onto a zoned neutral-atom architecture."""

    def __init__(
            self,
            benchmark: str,
            architecture: dict,
            initial_mapping: list,
            output_dir: str = "",
            scheduling_strategy: str = "asap_separate",
            placement_strategy: str = "baseline",
            routing_strategy: str = "baseline"
    ):
        """
        Args:
            benchmark: Path under ``benchmark/`` (with extension), e.g. ``qft/qft_n10.qasm``.
            architecture: Parsed JSON from ``architecture/<file>.json``.
            initial_mapping: Optional per-qubit (x, y) sites; empty list selects automatic layout.
            output_dir: Name of the subdirectory under ``results/`` for artifacts.
            scheduling_strategy: ``asap_separate`` or ``asap_joint``.
            placement_strategy: Passed to the router (reserved / experimental names warn and fall back).
            routing_strategy: Idle-qubit policy and related routing label for the placer/router.
        """
        self.benchmark = benchmark
        self.architecture = architecture
        self.given_initial_mapping = initial_mapping
        self.output_dir = output_dir
        self.stg_slm_sites = []
        self.ent_slm_sites = []
        self.scheduling_strategy = scheduling_strategy
        self.placement_strategy = placement_strategy
        self.routing_strategy = routing_strategy
        self.parse_benchmark()
        self.results_code = {
            'benchmark': self.benchmark_name,
            'output_dir': output_dir,
            'compilation_time': 0,
            'n_q': 0,
            'n_1q_gate': 0,
            'n_2q_gate': 0,
            'stages': {},
            'instructions': []
        }
        self.parse_slm_sites()
        print(
            f"[INFO] ZAP: architecture name={self.architecture.get('name', '?')!r}, "
            f"storage_traps={len(self.stg_slm_sites)}, entanglement_traps={len(self.ent_slm_sites)}"
        )
        self.set_program()

    def parse_benchmark(self):
        """Derive directory, stem, and file type from ``self.benchmark``."""
        benchmark_path = self.benchmark.replace("\\", "/")
        self.benchmark_dir, benchmark_filename = os.path.split(benchmark_path)
        self.benchmark_name, benchmark_ext = os.path.splitext(benchmark_filename)
        self.benchmark_type = benchmark_ext.lstrip(".")
        self.benchmark_stem = (
            f"{self.benchmark_dir}/{self.benchmark_name}"
            if self.benchmark_dir
            else self.benchmark_name
        )

    def get_sites(self, slm: dict):
        """Return all trap coordinates for one SLM block (grid from ``location``, ``r``, ``c``, ``site_seperation``)."""
        x, y = slm['location']
        sep_x, sep_y = slm['site_seperation']

        return [
            (x + i * sep_x, y + j * sep_y)
            for j in range(slm['r'])
            for i in range(slm['c'])
        ]

    def parse_slm_sites(self):
        """Populate storage and entanglement trap coordinates from the architecture JSON."""
        for zone in self.architecture['storage_zones']:
            for slm in zone['slms']:
                self.stg_slm_sites += self.get_sites(slm)
        self.stg_slm_sites = list(set(self.stg_slm_sites))

        for zone in self.architecture['entanglement_zones']:
            for slm in zone['slms']:
                self.ent_slm_sites += self.get_sites(slm)
        self.ent_slm_sites = list(set(self.ent_slm_sites))

    def set_program(self):
        """Load gates from QASM (transpiled to CZ+U) or JSON edge list; fill ``g_q`` and gate counts."""
        self.n_2q_gate = 0
        self.n_1q_gate = 0
        self.g_q = []
        benchmark_file = os.path.join(
            "benchmark",
            self.benchmark_dir,
            f"{self.benchmark_name}.{self.benchmark_type}"
        )
        with open(benchmark_file, 'r') as f:
            if self.benchmark_type == "qasm":
                qasm_str = f.read()
                if "OPENQASM 2" in qasm_str:
                    circuit = QuantumCircuit.from_qasm_str(qasm_str)
                elif "OPENQASM 3" in qasm_str:
                    circuit = parse(qasm_str)
                else:
                    raise ValueError("Unsupported QASM version detected.")

                # Strip trailing swaps left by Qiskit decomposition (not native to the atom model).
                swap_remain = True
                while swap_remain:
                    if circuit.data[-1][0].name == 'swap':
                        circuit.data.pop()
                    else:
                        swap_remain = False

                n_pre = circuit.num_qubits
                # High optimization_level is very slow on wide QFT-style circuits (minutes+).
                if n_pre <= 24:
                    opt_level = 3
                elif n_pre <= 64:
                    opt_level = 2
                elif n_pre <= 128:
                    opt_level = 1
                else:
                    opt_level = 0
                print(
                    f"[INFO] ZAP: QASM parsed ({n_pre} qubits), transpiling to CZ basis "
                    f"(optimization_level={opt_level}, may take a while)…"
                )
                cz_circuit = transpile(
                    circuit,
                    basis_gates=["cz", "id", "u2", "u1", "u3"],
                    optimization_level=opt_level,
                    seed_transpiler=0
                )
                print(
                    f"[INFO] ZAP: Transpile done — {cz_circuit.num_qubits} qubits, "
                    f"{len(cz_circuit.data)} operations in DAG"
                )
                instruction = cz_circuit.data
                self.results_code['n_q'] = cz_circuit.num_qubits
                for inst in instruction:
                    if inst.operation.num_qubits == 2:
                        self.results_code['n_2q_gate'] += 1
                        self.g_q.append((inst.qubits[0]._index, inst.qubits[1]._index))
                    elif inst.operation.name != "measure" and inst.operation.name != "barrier":
                        self.results_code['n_1q_gate'] += 1
                        self.g_q.append((inst.qubits[0]._index, inst.qubits[0]._index))
            elif self.benchmark_type == "json":
                graphs = json.load(f)
                for q0, q1 in graphs:
                    self.results_code['n_q'] = max(self.results_code['n_q'], q0, q1)
                    if q0 == q1:
                        self.g_q.append((q0, q1))
                        self.results_code['n_1q_gate'] += 1
                    else:
                        self.g_q.append((q0, q1))
                        self.results_code['n_2q_gate'] += 1
                self.results_code['n_q'] += 1
            else:
                raise ValueError("Unsupported benchmark file type.")


    def log_results(self, simulator: Simulator):
        """Append one simulation summary record to ``results/<output_dir>/log/<benchmark>.json``."""
        result = {
            "algorithm": self.benchmark_name,
            "n_qubits": simulator.n_q,
            "stage": self.current_stage,
            "n_1q_gate": simulator.n_1q_gate,
            "n_2q_gate": simulator.n_2q_gate,
            "total_duration": round(getattr(simulator, 'total_duration', 0), 6),
            "compilation_time": round(simulator.results_code['compilation_time'], 4),
            "total_fidelity": round(simulator.cir_fidelity, 8),
            "fidelity_1q_gate": round(simulator.cir_fidelity_1q_gate, 8),
            "fidelity_2q_gate": round(simulator.cir_fidelity_2q_gate, 8),
            "fidelity_idle": round(simulator.cir_fidelity_2q_gate_for_idle, 8),
            "fidelity_handover": round(simulator.cir_fidelity_atom_transfer, 8),
            "fidelity_decoherence": round(simulator.cir_fidelity_coherence, 8),
            "timestamp": datetime.now().isoformat()
        }
        
        os.makedirs(f"results/{self.output_dir}/log/", exist_ok=True)
        file_path = f"results/{self.output_dir}/log/{self.benchmark_name}.json"
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                data = json.load(f)
        else:
            data = []
        data.append(result)
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2)


    def solve(self, simulation: bool = False, animation: bool = False):
        """Schedule → route (placement + moves) → optional fidelity sim and MP4 animation."""
        print(f"[INFO] ZAP: Start solving {self.benchmark}")
        nq = self.results_code["n_q"]
        n_stg = len(self.stg_slm_sites)
        if nq > n_stg:
            raise ValueError(
                f"[Error] Circuit needs {nq} qubits after transpile but this architecture "
                f"defines only {n_stg} storage traps. For large QFT circuits set "
                f"\"architecture\": \"scale_to_500.json\" in the setting JSON "
                f"(see setting/scalability/qft.json)."
            )

        print(f"[INFO] ZAP: Start {self.scheduling_strategy} scheduling")
        tmp = time.time()
        scheduler = Scheduler(
            g_q=self.g_q,
            results_code=self.results_code
            )
        
        if self.scheduling_strategy == "asap_separate":
            scheduler.asap_separate()
        elif self.scheduling_strategy == "asap_joint":
            scheduler.asap_joint()
        else:
            raise ValueError("[Error] Unsupported scheduling strategy")

        self.results_code['compilation_time'] = time.time() - tmp
        list_gate = []
        self.current_stage = 0
        for gates in scheduler.list_scheduling:
            tmp = [self.g_q[i] for i in gates]
            list_gate.append(tmp)
            self.current_stage += 1

        print(f"[INFO] ZAP: Start placing and routing with \n\t\tplacement strategy: {self.placement_strategy} \n\t\trouting strategy: {self.routing_strategy}")
        
        router = Router(
            slm_sites=[self.stg_slm_sites, self.ent_slm_sites],
            results_code=self.results_code,
            list_full_gates=list_gate,
            qubit_mapping=self.given_initial_mapping,
            architecture=self.architecture,
            placement_strategy=self.placement_strategy,
            routing_strategy=self.routing_strategy
        )
        self.results_code = router.results_code

        if simulation:
            print("[INFO] ZAP: Start simulation")
            simulator = Simulator(
                results_code=self.results_code,
                architecture=self.architecture
            )
            self.log_results(simulator)

        if animation:
            print("[INFO] ZAP: Start animation")
            Animator(
                slm_sites=[self.stg_slm_sites, self.ent_slm_sites],
                results_code=self.results_code,
                architecture=self.architecture
            )

        print(f"[INFO] ZAP: Finish solving {self.benchmark}\n")
