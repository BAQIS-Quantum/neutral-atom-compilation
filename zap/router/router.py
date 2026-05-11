import math
import json
import os
import time
import warnings
from tqdm import tqdm
from copy import deepcopy

from zap.placer.placer import Placer

class Router:
    """Per-stage placement (via ``Placer``) and AOD move planning; emits the instruction list."""

    def __init__(self,
                 slm_sites: list,
                 results_code: dict,
                 list_full_gates: list,
                 qubit_mapping: list,
                 architecture: dict,
                 placement_strategy: str='baseline',
                 routing_strategy: str='baseline'
                 ):
        """
        Args:
            slm_sites: ``[storage_sites, entanglement_sites]`` as lists of ``(x, y)`` traps (µm).
            results_code: Shared dict with ``n_q``, ``stages``, ``benchmark``, ``output_dir``; extended with ``instructions``.
            list_full_gates: Scheduled gates per stage (pairs of qubit indices).
            qubit_mapping: Optional initial trap assignment per qubit index.
            architecture: Hardware timing/fidelity JSON plus optional ``routing`` overrides.
            placement_strategy: Reserved; layered modes warn and fall back in this tree.
            routing_strategy: Idle policy forwarded to ``Placer`` (e.g. ``baseline``, ``always_move``).
        """
        self.architecture = architecture
        operation_duration = self.architecture.get("operation_duration", {})
        routing_cfg = self.architecture.get("routing", {})
        movement_cfg = routing_cfg.get("movement", {})

        self.time_atom_transfer = operation_duration.get("atom_transfer", 15)
        self.time_2q_gate = operation_duration.get("2qGate", 0.25)
        self.time_1q_gate = operation_duration.get("1qGate", 0.5)

        self.PARKING_DIST = routing_cfg.get("parking_dist", 1)
        self.movement_mode = movement_cfg.get("mode", "linear")
        self.movement_acceleration = movement_cfg.get("acceleration", 0.00275)
        self.movement_avg_velocity = movement_cfg.get("avg_velocity", 0.55)

        self.slm_sites = slm_sites
        self.results_code = results_code
        self.n_q = results_code['n_q']
        self.list_full_gates = list_full_gates
        self.qubit_mapping = qubit_mapping

        self.placement_strategy = placement_strategy
        self.routing_strategy = routing_strategy

        tmp = time.time()
        self.route_qubit()
        self.results_code['compilation_time'] += time.time() - tmp
        self.log_results()

    def log_results(self):
        """
        Saves the results in JSON format within the specified directory.
        """
        output_dir = os.path.join("results", self.results_code['output_dir'], "code")
        output_path = os.path.join(
            output_dir, f"{self.results_code['benchmark']}_code.json"
        )
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(self.results_code, f)

    def write_init_instruction(self):
        """
        Writes initialization instructions for qubit mapping.
        """
        self.results_code['instructions'].clear()
        self.results_code['instructions'].append(
            {
                'type': "Init",
                'duration': [0 for _ in range(self.n_q)],
                'locs': [{
                    'id': q,
                    'x': self.current_mapping[q][0],
                    'y': self.current_mapping[q][1],
                } for q in range(self.n_q)]
            }
        )

    def write_1q_gate_instruction(self, gate_1q: list):
        """
        Writes instructions for single-qubit gates.

        Args:
            gate_1q (list): List of qubits for single-qubit gates.
        """
        qs = gate_1q
        locs = [
            {
                'id': q,
                'x': self.current_mapping[q][0],
                'y': self.current_mapping[q][1]
            } for q in qs
        ]
        self.results_code['instructions'].append({
            'type': "1qGate",
            'stage': self.index_list_gate,
            'duration': [self.time_1q_gate for _ in range(len(qs))],
            'qs': qs,
            'gates': gate_1q,
            'locs': locs
        })

    def write_2q_gate_instruction(self, gate_2q: list):
        """
        Writes instructions for two-qubit gates.

        Args:
            gate_2q (list): List of qubit pairs for two-qubit gates.
        """
        locs, qs = [], []
        for q0, q1 in gate_2q:
            qs += [q0, q1]
            locs.extend([{
                'id': q,
                'x': self.current_mapping[q][0],
                'y': self.current_mapping[q][1]
            } for q in [q0, q1]])

        self.results_code['instructions'].append({
            'type': "2qGate",
            'stage': self.index_list_gate,
            'duration': [self.time_2q_gate for _ in range(len(qs))],
            'qs': qs,
            'gates': gate_2q,
            'locs': locs
        })

    def write_activate_instruction(self, qs: list):
        """
        Writes instructions for activating qubits.
        """
        self.results_code['instructions'].append({
            'type': "Activate",
            'qs': qs,
            'duration': [self.time_atom_transfer for _ in range(len(qs))],
            'locs': [{
                'id': q,
                'x': self.current_mapping[q][0],
                'y': self.current_mapping[q][1]
            } for q in qs]
        })

    def write_deactivate_instruction(self, qs: list):
        """
        Writes instructions for deactivating qubits.
        """
        self.results_code['instructions'].append({
            'type': "Deactivate",
            'qs': qs,
            'duration': [self.time_atom_transfer for _ in range(len(qs))],
            'locs': [{
                'id': q,
                'x': self.current_mapping[q][0],
                'y': self.current_mapping[q][1]
            } for q in qs]
        })

    def write_move_instruction(self, qs: list, type: str, end_locs: list):
        """
        Writes instructions for moving qubits.
        """
        distance_list = [math.dist(self.current_mapping[q], end_locs[q]) for q in qs]
        duration_list = [self.movement_duration(d) for d in distance_list]
        self.results_code['instructions'].append(
            {
                'type': type,
                'qs': qs,
                'distance': distance_list,
                'duration': duration_list,
                'locs': [{
                    'id': q,
                    'x_begin': self.current_mapping[q][0],
                    'y_begin': self.current_mapping[q][1],
                    'x_end': end_locs[q][0],
                    'y_end': end_locs[q][1],
                    'movement': distance_list[i],
                } for i, q in enumerate(qs)]
            }
        )
        for q in qs:
            self.current_mapping[q] = end_locs[q]

    def movement_duration(self, d):
        """Heuristic move duration (µs) from distance ``d`` (µm); kept in sync with ``Placer.movement_duration``."""
        if d <= 0:
            return 0.0

        return 200*((d /110)**(1/2))

    def process_gate(self):
        """
        Processes the gates in the current stage.
        """
        gate_1q = [q0 for q0, q1 in self.list_gate if q0 == q1]
        gate_2q = [(q0, q1) for q0, q1 in self.list_gate if q0 != q1]
        if gate_1q:
            self.write_1q_gate_instruction(gate_1q)
        if gate_2q:
            self.write_2q_gate_instruction(gate_2q)
            self.write_idle(gate_2q)

    def write_idle(self, gate_2q: list):
        """
        Writes idle instructions for qubits that are not involved in the current stage.

        Args:
            gate_2q (list): List of qubit pairs for two-qubit gates.
        """
        idle_qubits_for_2q_gates = list(set(range(self.n_q)) - set([q for q0, q1 in gate_2q for q in [q0, q1]]))
        edge = min([y for _, y in self.slm_sites[1]])
        idle_qubits = []
        if idle_qubits_for_2q_gates:
            for q in idle_qubits_for_2q_gates:
                if self.current_mapping[q][1] >= edge:
                    idle_qubits.append(q)
        if idle_qubits:
            self.results_code['instructions'].append(
                {
                    'type': "Crosstalk",
                    'qs': idle_qubits,
                    'duration': [self.time_2q_gate for _ in range(len(idle_qubits))],
                    'locs': [{
                        'id': q,
                        'x': self.current_mapping[q][0],
                        'y': self.current_mapping[q][1]
                    } for q in idle_qubits]
                }
            )

    def process_rearrangement(self, aod_qubits: list):
        """Resolve column collisions, optional parking, then emit activate/move/deactivate for ``aod_qubits``."""
        activated_qs = []
        parking_end_qubits = []
        tmp_begin_locs = deepcopy(self.current_mapping)
        tmp_end_locs = deepcopy(self.final_mapping)
        while True:
            activate_xs = [self.current_mapping[q][0] for q in aod_qubits]
            activate_ys = [self.current_mapping[q][1] for q in aod_qubits]
            aod_qubits_xys = [self.current_mapping[q] for q in aod_qubits]

            activate_xys = [(x, y) for x in activate_xs for y in activate_ys]

            col_xs = []
            for x, y in activate_xys:
                if (x, y) in self.current_mapping and (x, y) not in aod_qubits_xys:
                    col_xs.append(x)
            col_xs = list(set(col_xs))

            parking_qs = [aod_qubits[i] for i in range(len(aod_qubits)) if activate_xs[i] in col_xs]

            if parking_qs:
                self.write_activate_instruction(parking_qs)
                activated_qs += parking_qs
                parking_qs = sorted(parking_qs, key=lambda x: self.current_mapping[x][0])
                parking_idx = [-len(parking_qs) // 2+i for i in range(len(parking_qs))]
                for i, q in enumerate(parking_qs):
                    tmp_begin_locs[q] = (
                        tmp_begin_locs[q][0] + self.PARKING_DIST * parking_idx[i],
                        tmp_begin_locs[q][1],
                    )
                    parking_end_qubits.append(q)
                self.write_move_instruction(parking_qs, "Park", tmp_begin_locs)
            else:
                break

        while True:
            add_activate = []
            add_begin_parking = []
            for q in aod_qubits:
                compatible, collision_type = self.verify_path(tmp_begin_locs[q], tmp_end_locs[q])
                if not compatible:
                    add_begin_parking.append(q)
                    if q not in activated_qs:
                        activated_qs.append(q)
                        add_activate.append(q)
                    if collision_type == "Vertical":
                        tmp_end_locs[q] = (
                            tmp_begin_locs[q][0] + self.PARKING_DIST,
                            tmp_begin_locs[q][1]
                        )
                        parking_end_qubits.append(q)
                    elif collision_type == "Horizontal":
                        tmp_end_locs[q] = (
                            tmp_begin_locs[q][0],
                            tmp_begin_locs[q][1] + self.PARKING_DIST
                        )
                        parking_end_qubits.append(q)
                    elif collision_type == "Diagonal":
                        tmp_end_locs[q] = (
                            tmp_end_locs[q][0] + self.PARKING_DIST,
                            tmp_end_locs[q][1]
                        )
                        parking_end_qubits.append(q)
                    else:
                        raise ValueError(f"Invalid collision type: {collision_type}")

            if add_activate:
                self.write_activate_instruction(add_activate)
            else:
                break

        if list(set(aod_qubits) - set(activated_qs)):
            self.write_activate_instruction(list(set(aod_qubits) - set(activated_qs)))

        self.write_move_instruction(aod_qubits, "BigMove", tmp_end_locs)

        if list(set(aod_qubits) - set(parking_end_qubits)):
            self.write_deactivate_instruction(list(set(aod_qubits) - set(parking_end_qubits)))

        if parking_end_qubits:
            self.write_move_instruction(list(set(parking_end_qubits)), "Park", self.final_mapping)
            self.write_deactivate_instruction(list(set(parking_end_qubits)))

    def verify_path(self, start, end):
        """
        Verifies the path between two points.

        Args:
            start (tuple): Starting point.
            end (tuple): Ending point.

        Returns:
            bool: True if the path is clear, False otherwise.
        """
        x1, y1 = start
        x2, y2 = end

        if x1 == x2:
            for y in range(min(y1, y2) + 1, max(y1, y2)):
                if (x1, y) in self.current_mapping:
                    return False, "Vertical"

        elif y1 == y2:
            for x in range(min(x1, x2) + 1, max(x1, x2)):
                if (x, y1) in self.current_mapping:
                    return False, "Horizontal"

        else:
            dx = x2 - x1
            dy = y2 - y1
            gcd = abs(dx) if dy == 0 else abs(dy) if dx == 0 else abs(math.gcd(dx, dy))
            step_x = dx // gcd
            step_y = dy // gcd

            x, y = x1 + step_x, y1 + step_y
            while (x, y) != (x2, y2):
                if (x, y) in self.current_mapping:
                    return False, "Diagonal"
                x += step_x
                y += step_y
        return True, "Clear"

    def compatible_2D(self, a, b):
        """
        Check if two vectors are compatible when routing.
        
        Args:
            a (tuple): (start_x, end_x, start_y, end_y)
            b (tuple): (start_x, end_x, start_y, end_y)
            
        Returns:
            bool: True if compatible, False otherwise.
        """
        if a[0] == b[0] and a[1] != b[1]:
            return False
        if a[1] == b[1] and a[0] != b[0]:
            return False
        if a[0] < b[0] and a[1] >= b[1]:
            return False
        if a[0] > b[0] and a[1] <= b[1]:
            return False

        if a[2] == b[2] and a[3] != b[3]:
            return False
        if a[3] == b[3] and a[2] != b[2]:
            return False
        if a[2] < b[2] and a[3] >= b[3]:
            return False
        if a[2] > b[2] and a[3] <= b[3]:
            return False

        return True

    def find_maximal_independent_set(self, vectors):
        """Greedy independent set on a conflict graph of 2D move tuples ``(sx, tx, sy, ty)``."""
        vectors = deepcopy(vectors)
        for vector in vectors:
            if (vector[1], vector[3]) in self.current_mapping:
                vectors.remove(vector)

        violations = []
        for i in range(len(vectors)):
            for j in range(i + 1, len(vectors)):
                if not self.compatible_2D(vectors[i], vectors[j]):
                    violations.append((i, j))

        conflict_graph = {i: set() for i in range(len(vectors))}
        for i, j in violations:
            conflict_graph[i].add(j)
            conflict_graph[j].add(i)

        degrees = sorted(conflict_graph.keys(), key=lambda x: len(conflict_graph[x]))
        independent_set = []
        visited = set()

        for node in degrees:
            if node not in visited:
                independent_set.append(node)
                visited.update(conflict_graph[node])
                visited.add(node)

        if independent_set:
            execute_vectors = [vectors[i] for i in independent_set]
            return execute_vectors
        else:
            raise ValueError("No compatible vectors found.")

    def route_qubit(self):
        """Greedy maximal compatible move batches between scheduled gate layers."""
        _layered = ("layered_fidelity", "windowed_union", "deferred_binding")
        if self.placement_strategy in _layered or self.routing_strategy in _layered:
            warnings.warn(
                "Layered placement/routing is not implemented in this repository; "
                "using the standard greedy router path.",
                UserWarning,
                stacklevel=2,
            )

        placer = Placer(
            self.slm_sites,
            self.results_code,
            self.list_full_gates,
            self.qubit_mapping,
            self.routing_strategy,
            self.architecture
        )
        self.current_mapping = list(placer.current_mapping)
        self.write_init_instruction()

        for index_list_gate, list_gate in enumerate(
                tqdm(self.list_full_gates, desc="\t\tProcessing")):
            self.index_list_gate = index_list_gate
            self.list_gate = list_gate

            self.final_mapping = placer.placing(self.index_list_gate)
            vectors, aod_qubits = [], []
            for q in range(self.n_q):
                if self.current_mapping[q] != self.final_mapping[q]:
                    aod_qubits.append(q)
                    vectors.append(
                        (self.current_mapping[q][0], self.final_mapping[q][0],
                         self.current_mapping[q][1], self.final_mapping[q][1]))
            while vectors:
                start_positions = set((v[0], v[2]) for v in vectors)
                safe_vectors = [v for v in vectors if (v[1], v[3]) not in start_positions]
                if not safe_vectors:
                    safe_vectors = [vectors[0]]
                execute_vectors = self.find_maximal_independent_set(safe_vectors)
                execute_aod_qubits = []
                for execute_vector in execute_vectors:
                    execute_idx = vectors.index(execute_vector)
                    execute_aod_qubits.append(aod_qubits[execute_idx])

                self.process_rearrangement(execute_aod_qubits)

                aod_qubits = [q for q in aod_qubits if q not in execute_aod_qubits]
                vectors = [vector for vector in vectors if vector not in execute_vectors]

            self.process_gate()