class Scheduler:
    """Build per-stage gate groups from a flat gate list ``g_q`` (indices into ``g_q``)."""

    def __init__(self,
                 g_q: list,
                 results_code: dict,
                 ):
        """
        Args:
            g_q: Flat list of gates as ``(q0, q1)`` with ``q0==q1`` for 1-qubit gates.
            results_code: Mutated in place; ``stages`` is filled by ``save_results``.
        """
        self.g_q = g_q
        self.results_code = results_code
        self.results_code['stages'] = {
            "num_stage": 0,
            "stage": {q: {} for q in range(self.results_code['n_q'])},
            "qs_status": {}
        }
        self.list_scheduling = []

    def asap_joint(self):
        """ASAP over the full gate stream: each gate starts when both qubits are free."""

        list_qubit_stage = [0 for _ in range(self.results_code['n_q'])]
        for i, gate in enumerate(self.g_q):
            stage0 = list_qubit_stage[gate[0]]
            stage1 = list_qubit_stage[gate[1]]
            stage = max(stage0, stage1)
            if stage >= len(self.list_scheduling):
                self.list_scheduling.append([])
            self.list_scheduling[stage].append(i)

            stage += 1
            list_qubit_stage[gate[0]] = stage
            list_qubit_stage[gate[1]] = stage
        self.save_results()

    def asap_separate(self):
        """ASAP schedule for all 2q layers first, then place 1q gates without reordering 2q stages."""
        list_qubit_stage = [0 for _ in range(self.results_code['n_q'])]

        two_qubit_gates = []
        single_qubit_gates = []

        for i, gate in enumerate(self.g_q):
            if gate[0] == gate[1]:
                single_qubit_gates.append((i, gate))
            else:
                two_qubit_gates.append((i, gate))

        for i, gate in two_qubit_gates:
            stage0 = list_qubit_stage[gate[0]]
            stage1 = list_qubit_stage[gate[1]]
            stage = max(stage0, stage1)
            if stage >= len(self.list_scheduling):
                self.list_scheduling.append([])
            self.list_scheduling[stage].append(i)

            stage += 1
            list_qubit_stage[gate[0]] = stage
            list_qubit_stage[gate[1]] = stage

        prev_2q_stage = {}
        for i, gate in enumerate(self.g_q):
            if i in [idx for sublist in self.list_scheduling for idx in sublist]:
                prev_2q_stage[gate[0]] = next(idx for idx, sublist in enumerate(self.list_scheduling) if i in sublist)
                prev_2q_stage[gate[1]] = next(idx for idx, sublist in enumerate(self.list_scheduling) if i in sublist)
            else:
                stage = max(prev_2q_stage.get(gate[0], -1), prev_2q_stage.get(gate[1], -1)) + 1

                while stage >= len(self.list_scheduling):
                    self.list_scheduling.append([])

                existing_two_qubit_gates = any(
                    self.g_q[j][0] != self.g_q[j][1] for j in self.list_scheduling[stage]
                )

                if existing_two_qubit_gates:
                    self.list_scheduling.insert(stage, [i])
                else:
                    self.list_scheduling[stage].append(i)

        self.save_results()

    def save_results(self):
        """Write ``list_scheduling`` into ``results_code['stages']`` for the placer/router."""
        stage_dict = {}
        for stage, gates in enumerate(self.list_scheduling):
            if all(self.g_q[gate][0] == self.g_q[gate][1] for gate in gates):
                stage_type = "1qGate"
            elif all(self.g_q[gate][0] != self.g_q[gate][1] for gate in gates):
                stage_type = "2qGate"
            else:
                stage_type = "mGate"
            stage_dict[stage] = {
                'type': stage_type,
                'idx': gates,
                'gates': [self.g_q[gate] for gate in gates]
            }

        qs_status = {q: [{"stage": stage_id, "status": None} for stage_id in range(len(self.list_scheduling))] for q in range(self.results_code['n_q'])}
        for stage, gates in enumerate(self.list_scheduling):
            for gate in gates:
                q0, q1 = self.g_q[gate]
                if q0 == q1:
                    qs_status[q0][stage]['status'] = "1qGate"
                else:
                    qs_status[q0][stage]['status'] = "2qGate"
                    qs_status[q1][stage]['status'] = "2qGate"

        self.results_code['stages']['qs_status'] = qs_status
        self.results_code['stages']['num_stage'] = len(self.list_scheduling)
        self.results_code['stages']['stage'] = stage_dict
