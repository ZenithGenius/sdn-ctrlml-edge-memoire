"""Moteur de simulation : rejoue un flux de requêtes sous une stratégie donnée.

Pipeline par époque (cf. §« Fonctionnement de la solution ») :
  collecte métriques → EMA (features) → K-means++ (clustering) → décision → action.
Modèle de contrôleur : file M/M/1 (temps de réponse = latence réseau + délai d'attente,
qui explose quand l'utilisation ρ approche 1 — reproduit la tendance d'Aly et al.).
"""
from __future__ import annotations

import argparse
import math

import numpy as np

from . import config
from .dataset import build_infrastructure, load_baseline_load, load_flow_stream
from .features import smoothed_vectors
from .modeling.baselines import make_strategy
from .modeling.clustering import classify
from .modeling.decision import Context


def _jain(values: list[float]) -> float:
    """Indice d'équité de Jain (1 = parfaitement équilibré)."""
    arr = np.asarray(values, dtype=float)
    denom = len(arr) * np.sum(arr**2)
    return float(np.sum(arr) ** 2 / denom) if denom > 0 else 1.0


def run(
    strategy: str = "edge",
    arrival_rate: float = 16000.0,
    n_flows: int = 20000,
    topo: str = config.DEFAULT_TOPOLOGY,
    n_controllers: int = config.N_CONTROLLERS,
    epoch: int = config.RECLUSTER_EVERY,
    alpha: float = config.EMA_ALPHA,
    seed: int = config.RANDOM_SEED,
    trace: list | None = None,
) -> dict:
    """Exécute une simulation et renvoie les métriques agrégées.

    `trace` : si une liste est fournie, l'état interne de chaque époque y est ajouté
    (charge brute, vecteurs lissés par EMA, clusters, silhouette). Sert à la démo
    interactive pour montrer le pipeline en fonctionnement ; sans effet sinon.
    """
    infra = build_infrastructure(topo, n_controllers, seed)
    controllers = infra.controllers
    mu = infra.capacity

    n_epochs = math.ceil(n_flows / epoch)
    baseline = load_baseline_load(controllers, n_epochs, seed)
    flows = load_flow_stream(n_flows, infra.nodes, seed)
    src_arr = flows["src"].to_numpy()
    cost_arr = flows["cost"].to_numpy()

    strat = make_strategy(strategy)
    ctx = Context(infra=infra, load={c: float(baseline[c].iloc[0]) for c in controllers})

    history: list[dict[int, dict[str, float]]] = []
    load_time_avg = {c: [] for c in controllers}     # ρ par époque -> moyenne (équilibrage)
    resp_times: list[float] = []
    served = 0
    migrations = 0
    last_ctrl: dict[int, int] = {}
    last_silhouette = float("nan")
    total_time = n_flows / arrival_rate              # s

    for e in range(n_epochs):
        lo, hi = e * epoch, min((e + 1) * epoch, n_flows)
        size_hint = max(1, hi - lo)
        cnt = {c: 0 for c in controllers}
        cost_sum = {c: 0.0 for c in controllers}
        lat_sum = {c: 0.0 for c in controllers}

        for i in range(lo, hi):
            src = int(src_arr[i])
            c = strat.assign(src, ctx)

            rho = ctx.load.get(c, 0.0)
            net = infra.latency[src][c]
            qdelay = (1000.0 / mu[c]) / max(1e-3, 1.0 - rho)   # délai d'attente M/M/1 (ms)
            resp_times.append(net + qdelay)
            if rho < 0.999:
                served += 1

            cnt[c] += 1
            cost_sum[c] += cost_arr[i]
            lat_sum[c] += net

            # Mise à jour incrémentale de la charge estimée (comme l'Algorithm 1 d'Aly
            # et al. : « Update load data H ») — sans elle, une stratégie join-shortest-
            # queue enverrait toute l'époque au même contrôleur (effet troupeau).
            ctx.load[c] = min(0.999, rho + (arrival_rate / size_hint) * cost_arr[i] / mu[c])

            if src in last_ctrl and last_ctrl[src] != c:
                migrations += 1
            last_ctrl[src] = c

        # --- Fin d'époque : recalcul de la charge réelle + snapshot multi-métriques ---
        snap: dict[int, dict[str, float]] = {}
        size = size_hint
        for c in controllers:
            share = cnt[c] / size
            lam_c = share * arrival_rate                      # flux/s vers c
            mean_cost = cost_sum[c] / cnt[c] if cnt[c] else 0.0
            demand = lam_c * mean_cost / mu[c]                 # charge dynamique normalisée
            base = float(baseline[c].iloc[e])
            rho = min(0.999, base + demand)

            snap[c] = {
                "cpu": rho,
                "mem": min(1.0, 0.8 * base + 0.2 * rho),
                "bw": min(1.0, demand),
                "latency": (lat_sum[c] / cnt[c]) if cnt[c] else 0.0,
                "nflows": share,
                # Intensité Packet-In normalisée par la capacité du contrôleur
                # (λ_c/µ_c) : distincte de `nflows` (part des flux) et sensible à
                # l'hétérogénéité — un même λ pèse plus lourd sur un petit contrôleur.
                "packetin": lam_c / mu[c],
            }
            ctx.load[c] = rho
            load_time_avg[c].append(rho)

        history.append(snap)

        # --- Analyse : EMA puis K-means++ (dès qu'on a ≥2 snapshots) ---
        if len(history) >= 2:
            vectors = smoothed_vectors(history, controllers, alpha)
            result = classify(vectors, config.N_CLUSTERS, config.METRICS, seed)
            ctx.cluster = result
            if not math.isnan(result.silhouette):
                last_silhouette = result.silhouette

        if trace is not None:
            cl = ctx.cluster
            trace.append({
                "epoch": e,
                "raw_cpu": {c: snap[c]["cpu"] for c in controllers},
                "share": {c: snap[c]["nflows"] for c in controllers},  # part des flux reçue
                # Les SIX métriques d'entrée, brutes puis lissées : ce sont elles que
                # consomment l'EMA et le K-means++. Sans cela, seule la charge CPU
                # était observable, alors que le multi-métriques est la contribution.
                "raw": {c: dict(snap[c]) for c in controllers},
                "ema": ({c: {m: float(vectors[i][config.METRICS.index(m)])
                             for m in config.METRICS}
                         for i, c in enumerate(controllers)}
                        if len(history) >= 2 else None),
                "ema_cpu": ({c: float(vectors[i][config.METRICS.index("cpu")])
                             for i, c in enumerate(controllers)}
                            if len(history) >= 2 else None),
                "labels": (dict(zip(controllers, cl.labels.tolist())) if cl else None),
                "overloaded": (sorted(cl.overloaded) if cl else []),
                "underutilized": (sorted(cl.underutilized) if cl else []),
                "load_score": (dict(cl.load_score) if cl else None),
                "silhouette": (cl.silhouette if cl else float("nan")),
            })

    mean_load = [float(np.mean(load_time_avg[c])) for c in controllers]
    return {
        "strategy": strategy,
        "arrival_rate": arrival_rate,
        "response_time_ms": float(np.mean(resp_times)),
        "throughput_fps": served / total_time,
        "cpu_util_mean": float(np.mean(mean_load)),
        "jain_fairness": _jain(mean_load),
        "migrations": migrations,
        "overload_ratio": float(np.mean([v >= 0.95 for v in mean_load])),
        "silhouette": last_silhouette,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Simulateur SDN_CTRLML-Edge")
    p.add_argument("--strategy", default="edge", choices=config.STRATEGIES)
    p.add_argument("--arrival-rate", type=float, default=16000.0)
    p.add_argument("--n-flows", type=int, default=20000)
    p.add_argument("--topo", default=config.DEFAULT_TOPOLOGY)
    p.add_argument("--seed", type=int, default=config.RANDOM_SEED)
    args = p.parse_args()

    m = run(
        strategy=args.strategy, arrival_rate=args.arrival_rate,
        n_flows=args.n_flows, topo=args.topo, seed=args.seed,
    )
    print(f"Stratégie      : {m['strategy']}")
    print(f"Taux d'arrivée : {m['arrival_rate']:.0f} flux/s")
    print(f"Temps réponse  : {m['response_time_ms']:.2f} ms")
    print(f"Débit servi    : {m['throughput_fps']:.0f} flux/s")
    print(f"CPU moyen      : {m['cpu_util_mean']:.3f}")
    print(f"Équité (Jain)  : {m['jain_fairness']:.3f}")
    print(f"Migrations     : {m['migrations']}")
    print(f"Silhouette     : {m['silhouette']:.3f}")


if __name__ == "__main__":
    main()
