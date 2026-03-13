import os
import matplotlib.pyplot as plt
import numpy as np

# Change this only if your output folders are somewhere else.
# If your folders like out_hbm2_gups_BASE are directly under ~/DRAMsim3,
# then "." is correct.
RESULTS_DIR = "."

memories = ["hbm2", "ddr", "lpddr"]
workloads = ["gups", "stream"]
configs = ["BASE", "CA1", "PHASE2"]

metrics = {
    "bandwidth": {},
    "latency": {},
    "energy": {},
    "power": {}
}


def extract_metrics(file_path):
    bandwidth = []
    latency = []
    energy = []
    power = []

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "average_bandwidth" in line:
                bandwidth.append(float(line.split("=")[1].split()[0]))

            elif "average_read_latency" in line:
                latency.append(float(line.split("=")[1].split()[0]))

            elif "total_energy" in line:
                energy.append(float(line.split("=")[1].split()[0]))

            elif "average_power" in line:
                power.append(float(line.split("=")[1].split()[0]))

    if not bandwidth or not latency or not energy or not power:
        raise ValueError(f"Missing one or more required metrics in {file_path}")

    return {
        "bandwidth": sum(bandwidth),
        "latency": sum(latency) / len(latency),
        "energy": sum(energy),
        "power": sum(power)
    }


# Collect data
for mem in memories:
    for wl in workloads:
        for cfg in configs:
            folder = os.path.join(RESULTS_DIR, f"out_{mem}_{wl}_{cfg}")
            txt = os.path.join(folder, "dramsim3.txt")

            if not os.path.exists(txt):
                print(f"Skipping missing file: {txt}")
                continue

            try:
                data = extract_metrics(txt)
            except Exception as e:
                print(f"Skipping {txt} due to parse error: {e}")
                continue

            label = f"{mem.upper()}-{wl.upper()}"

            for metric_name in metrics:
                if label not in metrics[metric_name]:
                    metrics[metric_name][label] = {}
                metrics[metric_name][label][cfg] = data[metric_name]


def plot_metric(metric_name, ylabel, title, output_name):
    desired_labels = [
        "HBM2-GUPS", "HBM2-STREAM",
        "DDR-GUPS", "DDR-STREAM",
        "LPDDR-GUPS", "LPDDR-STREAM"
    ]

    labels = []
    for label in desired_labels:
        if label in metrics[metric_name]:
            cfgs_present = metrics[metric_name][label]
            if "BASE" in cfgs_present and "CA1" in cfgs_present and "PHASE2" in cfgs_present:
                labels.append(label)

    if not labels:
        print(f"No complete data found for {metric_name}. Skipping plot.")
        return

    x = np.arange(len(labels))
    width = 0.25

    base_vals = [metrics[metric_name][label]["BASE"] for label in labels]
    ca1_vals = [metrics[metric_name][label]["CA1"] for label in labels]
    phase2_vals = [metrics[metric_name][label]["PHASE2"] for label in labels]

    plt.figure(figsize=(12, 6))
    plt.bar(x - width, base_vals, width, label="BASE")
    plt.bar(x, ca1_vals, width, label="CA-1")
    plt.bar(x + width, phase2_vals, width, label="Phase-2")

    plt.xticks(x, labels)
    plt.ylabel(ylabel)
    plt.title(title)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_name, dpi=300)
    plt.close()

    print(f"Generated {output_name}")


plot_metric("bandwidth", "Bandwidth (GB/s)", "Bandwidth Comparison", "bandwidth_comparison.png")
plot_metric("latency", "Latency (cycles)", "Latency Comparison", "latency_comparison.png")
plot_metric("energy", "Energy (pJ)", "Energy Comparison", "energy_comparison.png")
plot_metric("power", "Power (mW)", "Power Comparison", "power_comparison.png")

print("Done.")