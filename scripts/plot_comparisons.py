import argparse
import csv
import os
import re
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np


METRIC_PATTERNS = {
    "bandwidth": re.compile(r"average_bandwidth\s*=\s*([0-9.eE+-]+)"),
    "latency": re.compile(r"average_read_latency\s*=\s*([0-9.eE+-]+)"),
    "energy": re.compile(r"total_energy\s*=\s*([0-9.eE+-]+)"),
    "power": re.compile(r"average_power\s*=\s*([0-9.eE+-]+)"),
}


def parse_dramsim3_txt(txt_path: str) -> dict:
    """
    Parse per-channel metrics from dramsim3.txt and aggregate them.

    Aggregation policy:
    - Bandwidth: sum across channels
    - Energy: sum across channels
    - Power: sum across channels
    - Latency: mean across channels
    """
    bandwidth_vals = []
    latency_vals = []
    energy_vals = []
    power_vals = []

    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            bw_match = METRIC_PATTERNS["bandwidth"].search(line)
            if bw_match:
                bandwidth_vals.append(float(bw_match.group(1)))
                continue

            lat_match = METRIC_PATTERNS["latency"].search(line)
            if lat_match:
                latency_vals.append(float(lat_match.group(1)))
                continue

            energy_match = METRIC_PATTERNS["energy"].search(line)
            if energy_match:
                energy_vals.append(float(energy_match.group(1)))
                continue

            power_match = METRIC_PATTERNS["power"].search(line)
            if power_match:
                power_vals.append(float(power_match.group(1)))
                continue

    if not bandwidth_vals:
        raise ValueError(f"No bandwidth entries found in {txt_path}")
    if not latency_vals:
        raise ValueError(f"No latency entries found in {txt_path}")
    if not energy_vals:
        raise ValueError(f"No energy entries found in {txt_path}")
    if not power_vals:
        raise ValueError(f"No power entries found in {txt_path}")

    metrics = {
        "bandwidth": sum(bandwidth_vals),
        "latency": sum(latency_vals) / len(latency_vals),
        "energy": sum(energy_vals),
        "power": sum(power_vals),
    }
    return metrics


def load_manifest(csv_path: str) -> list:
    """
    Expected CSV columns:
    memory,workload,config_label,results_dir
    """
    rows = []
    required = {"memory", "workload", "config_label", "results_dir"}

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("Manifest CSV is empty or missing headers.")
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"Manifest missing columns: {sorted(missing)}")

        for row in reader:
            rows.append({
                "memory": row["memory"].strip(),
                "workload": row["workload"].strip(),
                "config_label": row["config_label"].strip(),
                "results_dir": row["results_dir"].strip(),
            })

    return rows


def collect_metrics(manifest_rows: list) -> dict:
    """
    Returns:
    data[(memory, workload, config_label)] = metrics_dict
    """
    data = {}
    for row in manifest_rows:
        results_dir = row["results_dir"]
        txt_path = os.path.join(results_dir, "dramsim3.txt")

        if not os.path.isfile(txt_path):
            raise FileNotFoundError(
                f"Could not find dramsim3.txt in results directory: {results_dir}"
            )

        metrics = parse_dramsim3_txt(txt_path)
        key = (row["memory"], row["workload"], row["config_label"])
        data[key] = metrics

    return data


def plot_metric(data: dict, metric_name: str, ylabel: str, title: str, output_path: str,
                memories_order: list, workloads_order: list, config_order: list) -> None:
    x_labels = []
    for memory in memories_order:
        for workload in workloads_order:
            x_labels.append(f"{memory}-{workload}")

    x = np.arange(len(x_labels))
    width = 0.22

    fig, ax = plt.subplots(figsize=(12, 6))

    for idx, config_label in enumerate(config_order):
        values = []
        for memory in memories_order:
            for workload in workloads_order:
                key = (memory, workload, config_label)
                if key not in data:
                    raise KeyError(f"Missing data for {key}")
                values.append(data[key][metric_name])

        offset = (idx - (len(config_order) - 1) / 2) * width
        ax.bar(x + offset, values, width, label=config_label)

    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, rotation=0)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=300)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Generate DRAMsim3 comparison plots from multiple result folders."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to CSV file listing memory/workload/config/result directory mappings.",
    )
    parser.add_argument(
        "--outdir",
        default="plots",
        help="Directory where PNG plots will be saved.",
    )
    parser.add_argument(
        "--memories",
        nargs="+",
        default=["HBM2", "DDR4", "LPDDR4"],
        help="Memory order for plotting.",
    )
    parser.add_argument(
        "--workloads",
        nargs="+",
        default=["GUPS", "STREAM"],
        help="Workload order for plotting.",
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        default=["BASE", "CA-1", "Phase-2"],
        help="Configuration label order for plotting.",
    )

    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    manifest_rows = load_manifest(args.manifest)
    data = collect_metrics(manifest_rows)

    plot_metric(
        data=data,
        metric_name="bandwidth",
        ylabel="Bandwidth (GB/s)",
        title="Bandwidth Comparison",
        output_path=os.path.join(args.outdir, "bandwidth_comparison.png"),
        memories_order=args.memories,
        workloads_order=args.workloads,
        config_order=args.configs,
    )

    plot_metric(
        data=data,
        metric_name="latency",
        ylabel="Latency (cycles)",
        title="Latency Comparison",
        output_path=os.path.join(args.outdir, "latency_comparison.png"),
        memories_order=args.memories,
        workloads_order=args.workloads,
        config_order=args.configs,
    )

    plot_metric(
        data=data,
        metric_name="energy",
        ylabel="Energy (pJ)",
        title="Energy Comparison",
        output_path=os.path.join(args.outdir, "energy_comparison.png"),
        memories_order=args.memories,
        workloads_order=args.workloads,
        config_order=args.configs,
    )

    plot_metric(
        data=data,
        metric_name="power",
        ylabel="Power (mW)",
        title="Power Comparison",
        output_path=os.path.join(args.outdir, "power_comparison.png"),
        memories_order=args.memories,
        workloads_order=args.workloads,
        config_order=args.configs,
    )

    print(f"Plots saved in: {args.outdir}")


if __name__ == "__main__":
    main()
