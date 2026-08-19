"""Generate the DL-reasoning figure for the paper.

Reads the scored per-run file and writes a PDF into the paper's Fig/ directory.
Matplotlib PDF rather than TikZ/pgfplots, per the workspace figure convention, so
the figure regenerates whenever the numbers change.

Usage:
    python make_figure.py --scored scored_all.jsonl --out ../../paper/Fig/dl_reasoning.pdf
"""
import argparse, collections, json, math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ARMS = [("none", "no tool"), ("lookup", "lookup only"), ("dlquery", "reasoning")]
ARM_COLORS = {"none": "#c7c7c7", "lookup": "#7fa8d1", "dlquery": "#1f4e79"}


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0, 0.0
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return ph, max(0.0, c - h), min(1.0, c + h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", default="scored_all.jsonl")
    ap.add_argument("--out", default="../../paper/Fig/dl_reasoning.pdf")
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(a.scored) if l.strip()]
    models = sorted({r["model"] for r in rows})
    tasks = ["T1", "T2"]

    fig, axes = plt.subplots(1, len(tasks), figsize=(7.0, 2.9), sharey=True)
    for ax, task in zip(axes, tasks):
        xs, labels = [], []
        for i, m in enumerate(models):
            for j, (arm, _) in enumerate(ARMS):
                sub = [r for r in rows if r["model"] == m and r["condition"] == arm
                       and r["task"] == task]
                if not sub:
                    continue
                k = sum(1 for r in sub if r["exact_set"])
                ph, lo, hi = wilson(k, len(sub))
                x = i * (len(ARMS) + 1) + j
                xs.append(x)
                ax.bar(x, ph * 100, color=ARM_COLORS[arm], width=0.85,
                       edgecolor="white", linewidth=0.5)
                # Clamp: at k == n the Wilson upper bound lands a hair under 1.0
                # (0.99997), which makes the error bar marginally negative.
                yerr = [[max(0.0, (ph - lo) * 100)], [max(0.0, (hi - ph) * 100)]]
                ax.errorbar(x, ph * 100, yerr=yerr,
                            fmt="none", ecolor="#333333", elinewidth=0.8, capsize=2)
            labels.append((i * (len(ARMS) + 1) + 1, m.split("/")[-1]))
        ax.set_xticks([p for p, _ in labels])
        ax.set_xticklabels([l for _, l in labels], fontsize=7)
        ax.set_ylim(0, 105)
        ax.set_title("%s: %s" % (task, "subsumption" if task == "T1" else "existential"),
                     fontsize=8)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(labelsize=7)
    axes[0].set_ylabel("exact answer-set match (%)", fontsize=8)
    handles = [plt.Rectangle((0, 0), 1, 1, color=ARM_COLORS[a_]) for a_, _ in ARMS]
    axes[-1].legend(handles, [lab for _, lab in ARMS], fontsize=7, frameon=False,
                    loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=3)
    fig.tight_layout()
    fig.savefig(a.out, bbox_inches="tight")
    print("wrote %s (%d models, error bars are 95%% Wilson CIs)" % (a.out, len(models)))


if __name__ == "__main__":
    main()
