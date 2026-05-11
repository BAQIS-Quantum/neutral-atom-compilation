import numpy as np

class Simulator:
    """Roll up duration and multiplicative fidelity from a compiled ``results_code`` instruction list."""

    def __init__(
        self,
        results_code: dict,
        architecture: dict):
        """
        Args:
            results_code: Output of ``Router`` (``instructions``, counts, ``n_q``).
            architecture: Same JSON used at compile time for timing/fidelity parameters.
        """
        self.results_code = results_code
        self.architecture = architecture
        self.init_param()
        
        self.simulate()

    def init_param(self):
        """Load hardware knobs and reset per-circuit accumulators."""
        self.n_q = self.results_code['n_q']
        self.n_1q_gate = self.results_code['n_1q_gate']
        self.n_2q_gate = self.results_code['n_2q_gate']

        operation_fidelity = self.architecture.get('operation_fidelity', {})
        operation_duration = self.architecture.get('operation_duration', {})
        qubit_spec = self.architecture.get('qubit_spec', {})

        self.fidelity_2q_gate = operation_fidelity.get('two_qubit_gate', 0.999)
        self.fidelity_2q_gate_for_idle = operation_fidelity.get(
            'two_qubit_gate_for_idle',
            1 - (1-self.fidelity_2q_gate)/2,
        )
        self.fidelity_1q_gate = operation_fidelity.get('single_qubit_gate', 0.999)
        self.fidelity_atom_transfer = operation_fidelity.get('atom_transfer', 0.999)

        self.time_coherence = qubit_spec.get('T2', 1.5e6)
        self.time_atom_transfer = operation_duration.get('atom_transfer', 15)
        self.time_2q_gate = operation_duration.get('2qGate', 0.25)
        self.time_1q_gate = operation_duration.get('1qGate', 0.5)

        print(f"\t\tParameters\n\t\tTime: T2 = {self.time_coherence} us, 2q_gate = {self.time_2q_gate} us, 1q_gate = {self.time_1q_gate} us, atom_transfer = {self.time_atom_transfer} us\n\t\tFidelity: 2q_gate = {self.fidelity_2q_gate}, 2q_gate_for_idle = {self.fidelity_2q_gate_for_idle}, 1q_gate = {self.fidelity_1q_gate}, atom_transfer = {self.fidelity_atom_transfer}")

        self.cir_fidelity = 1
        self.cir_fidelity_2q_gate = 1
        self.cir_fidelity_2q_gate_for_idle = 1
        self.cir_fidelity_1q_gate = 1
        self.cir_fidelity_atom_transfer = 1
        self.cir_fidelity_coherence = 1
        self.cir_qubit_busy_time = [0 for _ in range(self.n_q)]
        self.cir_qubit_movement_duration = [0 for _ in range(self.n_q)]

        self.total_duration = 0
        self.total_movement_duration = 0

    def simulate(self):
        """Walk instructions to accumulate wall-clock and fidelity factors."""
        for instruction in self.results_code['instructions']:
            duration = instruction['duration']
            if instruction['type'] == "Init":
                continue
            elif instruction['type'] == "Activate" or instruction['type'] == "Deactivate":
                self.total_duration += max(instruction['duration'])
                self.cir_fidelity_atom_transfer *= pow(self.fidelity_atom_transfer, len(instruction['qs']))
                for i, q in enumerate(instruction['qs']):
                    self.cir_qubit_busy_time[q] += duration[i]
            elif instruction['type'] == "BigMove":
                self.total_duration += max(instruction['duration'])
                self.total_movement_duration += max(instruction['duration'])
                for i, q in enumerate(instruction['qs']):
                    self.cir_qubit_busy_time[q] += duration[i]
                    self.cir_qubit_movement_duration[q] += duration[i]
            elif instruction['type'] == "Park":
                self.total_duration += max(instruction['duration'])
                for i, q in enumerate(instruction['qs']):
                    self.cir_qubit_busy_time[q] += duration[i]
                    self.cir_qubit_movement_duration[q] += duration[i]
            elif instruction['type'] == "1qGate":
                self.total_duration += max(instruction['duration'])
                self.cir_fidelity_1q_gate *= pow(self.fidelity_1q_gate, len(instruction['gates']))
                for i, q in enumerate(instruction['qs']):
                    self.cir_qubit_busy_time[q] += duration[i]
            elif instruction['type'] == "2qGate":
                self.total_duration += max(instruction['duration'])
                self.cir_fidelity_2q_gate *= pow(self.fidelity_2q_gate, len(instruction['gates']))
                for i, q in enumerate(instruction['qs']):
                    self.cir_qubit_busy_time[q] += duration[i]
            elif instruction['type'] == "Crosstalk":
                self.cir_fidelity_2q_gate_for_idle *= pow(self.fidelity_2q_gate_for_idle, len(instruction['qs']))
                for i, q in enumerate(instruction['qs']):
                    self.cir_qubit_busy_time[q] += duration[i]
            else:
                raise ValueError("Wrong instruction type")

        for t in self.cir_qubit_busy_time:
            cir_qubit_idle_time = self.total_duration - t
            self.cir_fidelity_coherence *= np.exp(- cir_qubit_idle_time / self.time_coherence)


        self.cir_fidelity = self.cir_fidelity_1q_gate * self.cir_fidelity_2q_gate * self.cir_fidelity_2q_gate_for_idle \
                                * self.cir_fidelity_atom_transfer * self.cir_fidelity_coherence