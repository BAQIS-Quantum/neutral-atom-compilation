from zap.zap import Zap
import argparse
import json
import os

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "setting",
        metavar="S",
        type=str,
        nargs="?",  
        default="default",
        help="File name (without extension) located in ./setting/, e.g., default")
    parser.add_argument(
        "--scheduling_strategy", 
        help="scheduling strategy: asap_separate, asap_joint", 
        type=str, 
        default="asap_separate")
    parser.add_argument(
        "--placement_strategy",
        help="placement strategy: baseline, fidelity_first, hotspot_guard, layered_fidelity, windowed_union, auto",
        type=str,
        default="baseline")
    parser.add_argument(
        "--routing_strategy",
        help=(
            "routing strategy: baseline, fidelity_first, gated_fidelity, "
            "layered_fidelity, windowed_union, auto; "
            "also always_move, always_stay, lookahead (idle qubit storage vs entanglement zone)"
        ),
        type=str,
        default="baseline")
    args = parser.parse_args()

    setting_path = f"./setting/{args.setting}.json"
    if not os.path.exists(setting_path):
        raise FileNotFoundError(f"Configuration file {setting_path} does not exist")
    with open(setting_path, 'r') as f:
        param = json.load(f)

    if os.path.basename(setting_path) == "default.json":
        param["output_dir"] = "default"
    else:
        # Mirror preset path, e.g. scalability/qft -> results/scalability/qft/
        param.setdefault("output_dir", args.setting.replace("\\", "/"))

    arch_path = f"./architecture/{param['architecture']}"
    with open(arch_path, 'r') as f:
        architecture = json.load(f)
    print(f"[INFO] Loaded architecture file: {arch_path}")
    # Optional override from setting file for routing-related knobs.
    if 'routing_cfg' in param and isinstance(param['routing_cfg'], dict):
        architecture.setdefault('routing', {})
        architecture['routing'].update(param['routing_cfg'])

    for benchmark in param['benchmark']:
        zap = Zap(
            benchmark=benchmark,
            architecture=architecture,
            initial_mapping=param['initial_mapping'] if 'initial_mapping' in param else [],
            output_dir=param['output_dir'],
            scheduling_strategy=args.scheduling_strategy,
            placement_strategy=args.placement_strategy,
            routing_strategy=args.routing_strategy
            )

        zap.solve(
            param.get("simulation", True),
            param.get("animation", False),
        )
