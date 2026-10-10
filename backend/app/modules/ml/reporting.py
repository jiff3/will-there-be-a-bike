"""Small static charts and machine-readable comparisons from actual predictions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.modules.ml.artifacts import write_json


def render_report(report: dict[str, Any], directory: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "evaluation.json", report)
    tasks = report["tasks"]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    for ax, (key, task) in zip(axes.flat, tasks.items(), strict=True):
        names = list(task["test"])
        ax.bar(names, [task["test"][n]["mae"] for n in names], color="#087f8c")
        ax.set(title=key, ylabel="Held-out MAE (rides)")
        ax.tick_params(axis="x", rotation=22, labelsize=8)
        ax.grid(axis="y", alpha=0.2)
    fig.suptitle("Final-period count forecasts — lower MAE is better")
    fig.savefig(directory / "comparisons.svg")
    plt.close(fig)
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    for ax, (key, task) in zip(axes.flat, tasks.items(), strict=True):
        daily = task["daily_backtest"]
        for name in ("seasonal", "recent", task["selected_archive"]):
            ax.plot(list(daily), [v[name]["mae"] for v in daily.values()], label=name)
        ax.set(title=key, ylabel="Daily held-out MAE")
        ax.tick_params(axis="x", rotation=40, labelsize=7)
        ax.legend(fontsize=8)
    fig.savefig(directory / "daily-backtest.svg")
    plt.close(fig)
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    for ax, (key, task) in zip(axes.flat, tasks.items(), strict=True):
        groups = task["strata"]
        name = task["selected_archive"]
        ax.bar(list(groups), [v[name]["mae"] for v in groups.values()], color="#b45309")
        ax.set(title=f"{key}: {name}", ylabel="MAE by condition")
        ax.tick_params(axis="x", rotation=25, labelsize=7)
    fig.savefig(directory / "conditions.svg")
    plt.close(fig)
    lines = [
        "# Real-data forecasting evaluation",
        "",
        f"Artifact: `{report['version']}`",
        "",
        f"Data SHA-256: `{report['data_sha256']}`",
        "",
        "These are retrospective event-time count evaluations, not historical online deployments.",
        "Recent-count inputs assume a completed-trip provider with the configured delay;",
        "public GBFS does not supply them. Archive-only forecasts do not require these inputs.",
        "",
        "Selection uses early validation MAE. Late validation calibrates intervals.",
        "Final test is never fitted or tuned; its only selection role is a one-way rejection gate",
        "to retain a baseline when the candidate fails to beat it.",
        "",
        "| Target | Model | Test MAE | RMSE | Poisson deviance |",
        "|---|---|---:|---:|---:|",
    ]
    for key, task in tasks.items():
        for name, value in task["test"].items():
            lines.append(
                f"| {key} | {name} | {value['mae']:.4f} | {value['rmse']:.4f} | "
                f"{value['poisson_deviance']:.4f} |"
            )
    lines += [
        "",
        "![Model comparisons](comparisons.svg)",
        "",
        "![Daily replay](daily-backtest.svg)",
        "",
        "![Conditions](conditions.svg)",
        "",
        "## Accepted models and uncertainty",
        "",
    ]
    for key, task in tasks.items():
        name = task["selected_archive"]
        coverage = task["intervals"][name]
        lines.append(
            f"- {key}: archive **{name}**, timely-history **{task['selected_history']}**; "
            f"archive interval coverage {coverage['empirical_coverage']:.3%}, "
            f"mean width {coverage['mean_width']:.2f} rides."
        )
    lines += [
        "",
        "Full per-station, zero/positive-demand, and low/high-station-demand metrics,",
        "global gain importance, daily backtests, source identities, training configuration",
        "and measured resource use are in `evaluation.json`.",
        "",
        "Intervals are residual-based estimates with empirical temporal coverage, not guarantees.",
        "Simulated empty/full risk is NOT empirically calibrated stockout probability.",
    ]
    (directory / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
