"""Figures du chapitre « Résultats et commentaires » (CCDS `plots.py`).

Reproduit le format des figures d'Aly et al. (2025) : temps de réponse vs taux
d'arrivée, équilibrage (Jain), utilisation CPU. Backend headless (Agg).
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from . import config  # noqa: E402

_LABELS = {
    "edge": "SDN_CTRLML-Edge (proposé)",
    "sdnctrlml": "SDN_CTRLML (K-means, CPU)",
    "onos": "ONOS statique",
    "rr": "Round-Robin",
    "random": "Random",
}


def _line_by_strategy(df: pd.DataFrame, y: str, ylabel: str, title: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for strat, sub in df.groupby("strategy"):
        sub = sub.sort_values("arrival_rate")
        ax.plot(sub["arrival_rate"] / 1000, sub[y], marker="o",
                label=_LABELS.get(strat, strat))
    ax.set_xlabel("Taux d'arrivée (×1000 flux/s)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_all(df: pd.DataFrame, out_dir: Path = config.FIGURES_DIR) -> list[Path]:
    """Génère toutes les figures et renvoie les chemins produits."""
    out_dir.mkdir(parents=True, exist_ok=True)
    figs = {
        "response_time.png": ("response_time_ms", "Temps de réponse (ms)",
                              "Temps de réponse vs charge"),
        "fairness.png": ("jain_fairness", "Indice d'équité de Jain",
                         "Équilibrage de charge entre contrôleurs"),
        "cpu_utilization.png": ("cpu_util_mean", "Utilisation CPU moyenne",
                                "Utilisation des contrôleurs vs charge"),
        "throughput.png": ("throughput_fps", "Débit servi (flux/s)",
                           "Débit vs charge"),
    }
    paths = []
    for fname, (y, ylabel, title) in figs.items():
        p = out_dir / fname
        _line_by_strategy(df, y, ylabel, title, p)
        paths.append(p)
    return paths
