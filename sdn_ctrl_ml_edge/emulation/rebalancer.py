"""Rééquilibreur du PoC : applique le VRAI algorithme du mémoire sur l'émulation.

Boucle : lire la charge REST de chaque contrôleur -> classer les contrôleurs avec
`clustering.classify` (K-means++ multi-métriques + seuil OVERLOAD_CPU, exactement comme
le simulateur) -> déplacer chaque switch d'un contrôleur surchargé vers le contrôleur
sain le moins chargé, via `ovs-vsctl set-controller`.

La décision (`plan_reassignments`) est une fonction pure, testée sans root ni Ryu
(tests/test_emulation.py). Seules la lecture REST et l'application `ovs-vsctl` demandent
l'exécution (dans le conteneur Containernet, en root).
"""
from __future__ import annotations

import logging

import numpy as np

from .. import config
from ..modeling.clustering import classify

# Métriques d'émulation mappées sur les noms attendus par le clustering du mémoire.
# "cpu" = charge relative : 1.0 = le contrôleur porte exactement sa part équitable.
EMU_METRICS = ["cpu", "packetin", "nflows"]

log = logging.getLogger(__name__)

# Un contrôleur est « surchargé » s'il porte au moins 1,5 fois sa part équitable.
# (Le simulateur, lui, raisonne en charge CPU absolue avec config.OVERLOAD_CPU.)
EMU_OVERLOAD_RATIO = 1.5


def smooth_loads(loads, prev_ema=None, alpha=config.EMA_ALPHA):
    """Lissage EMA des charges mesurées : `EMA_t = α·X_t + (1−α)·EMA_{t−1}`.

    Indispensable en émulation réelle : juste après une migration, les tables de flux
    du switch déplacé sont vides et regénèrent une rafale de Packet-In. Sans lissage,
    l'algorithme prend ce transitoire pour une surcharge durable et sur-corrige, au
    point de vider complètement un contrôleur. C'est la justification concrète de la
    couche EMA du mémoire, que le simulateur (sans bruit de mesure) ne fait pas ressortir.
    """
    prev_ema = prev_ema or {}
    out = {}
    for c, d in loads.items():
        x = d["packetin_rate_total"]
        prev = prev_ema.get(c)
        ema = x if prev is None else alpha * x + (1.0 - alpha) * prev
        out[c] = ema
    return out


def controller_vectors(loads, capacity):
    """Construit la matrice [n_contrôleurs x 3] à partir des charges REST.

    `loads[cid]` = {"packetin_rate_total": float, "flows_total": float}.
    `capacity[cid]` = débit de service du contrôleur (hétérogène Cloud/Fog/Edge).

    La charge « cpu » est RELATIVE : part de la charge totale du plan de contrôle,
    pondérée par la capacité du contrôleur. En émulation, les débits absolus
    (dizaines de Packet-In/s) sont sans commune mesure avec les capacités du
    simulateur (milliers de req/s) : une normalisation absolue ne dépasserait jamais
    le seuil de surcharge. Le critère relatif est lui indépendant de l'échelle.
    """
    controllers = sorted(loads)
    rates = {c: loads[c]["packetin_rate_total"] for c in controllers}
    total = sum(rates.values())
    # Part « équitable » de chaque contrôleur, au prorata de sa capacité.
    cap_total = sum(max(1.0, capacity[c]) for c in controllers) or 1.0

    vecs = np.zeros((len(controllers), len(EMU_METRICS)))
    for i, c in enumerate(controllers):
        fair = total * (max(1.0, capacity[c]) / cap_total)   # charge attendue si équilibré
        # 1.0 = le contrôleur porte exactement sa part ; > 1 = il en porte plus.
        cpu = (rates[c] / fair) if fair > 0 else 0.0
        vecs[i] = [cpu, rates[c], loads[c]["flows_total"]]
    return controllers, vecs


def plan_reassignments(loads, assignment, switch_load, capacity, seed=config.RANDOM_SEED,
                       loads_raw=None):
    """Décide quels switches déplacer et vers quel contrôleur. FONCTION PURE.

    - `assignment[switch]` = contrôleur maître courant du switch.
    - `switch_load[switch]` = charge (packet-in rate) attribuable à ce switch.
    - `loads` : charges LISSÉES (EMA) ; servent à décider des délestages.
    - `loads_raw` : charges brutes ; servent à repérer une capacité inutilisée.

    Renvoie `(moves, decision)` :
      - `moves` : {switch: nouveau_contrôleur}
      - `decision` : sortie du clustering (labels, profils, silhouette) pour la trace / visu.
    """
    import math

    controllers, vecs = controller_vectors(loads, capacity)
    result = classify(vecs, config.N_CLUSTERS, EMU_METRICS, seed,
                      overload_cpu=EMU_OVERLOAD_RATIO)

    overloaded = {controllers[i] for i in range(len(controllers))
                  if result.labels[i] in result.overloaded}
    under = {controllers[i] for i in range(len(controllers))
             if result.labels[i] in result.underutilized}

    cluster = {controllers[i]: int(result.labels[i]) for i in range(len(controllers))}
    profile = {}
    for c in controllers:
        if c in overloaded:
            profile[c] = "surchargé"
        elif c in under:
            profile[c] = "sous-utilisé"
        else:
            profile[c] = "normal"
    sil = result.silhouette
    decision = {
        "cluster": {str(c): cluster[c] for c in controllers},
        "profile": {str(c): profile[c] for c in controllers},
        "silhouette": None if (sil != sil or math.isnan(sil)) else round(float(sil), 4),
        "k": int(result.k),
    }

    live = {c: loads[c]["packetin_rate_total"] for c in controllers}
    healthy = [c for c in controllers if c not in overloaded]
    if not healthy:                                        # tout est surchargé : ne rien casser
        return {}, decision

    # Part équitable de chaque contrôleur (au prorata de sa capacité) : sert de cible.
    total = sum(live.values())
    cap_total = sum(max(1.0, capacity[c]) for c in controllers) or 1.0
    fair = {c: total * (max(1.0, capacity[c]) / cap_total) for c in controllers}

    moves = {}
    # --- Phase 1 : délester les contrôleurs SURCHARGÉS ---------------------
    for c in sorted(overloaded):
        remaining = live[c]
        own = sorted((s for s, host in assignment.items() if host == c),
                     key=lambda s: switch_load.get(s, 0.0), reverse=True)
        for s in own:
            if remaining <= fair[c]:
                break
            load_s = switch_load.get(s, 0.0)
            ok = [h for h in healthy if live[h] + load_s <= 1.2 * fair[h]]
            if not ok:
                break
            target = min(ok, key=lambda h: live[h] / max(1e-9, fair[h]))
            moves[s] = target
            live[target] += load_s
            remaining -= load_s
        live[c] = remaining

    # --- Phase 2 : REMPLIR les contrôleurs sous-utilisés --------------------
    if loads_raw:
        for c in controllers:
            live[c] = loads_raw[c]["packetin_rate_total"] - (
                sum(switch_load.get(s, 0.0) for s, t in moves.items() if t != c and
                    assignment.get(s) == c))
            live[c] = max(0.0, live[c]) + sum(
                switch_load.get(s, 0.0) for s, t in moves.items() if t == c)
        total = sum(loads_raw[c]["packetin_rate_total"] for c in controllers)
        fair = {c: total * (max(1.0, capacity[c]) / cap_total) for c in controllers}

    for c in sorted(controllers, key=lambda h: live[h] / max(1e-9, fair[h])):
        if live[c] >= 0.5 * fair[c]:
            continue
        for _ in range(len(controllers)):
            donor = max(controllers, key=lambda h: live[h] / max(1e-9, fair[h]))
            if donor == c or live[donor] <= 1.2 * fair[donor]:
                break
            cand = [s for s, host in assignment.items()
                    if host == donor and s not in moves and switch_load.get(s, 0.0) > 0]
            cand += [s for s, tgt in moves.items() if tgt == donor]
            if not cand:
                break
            s = min(cand, key=lambda x: switch_load.get(x, 0.0))
            load_s = switch_load.get(s, 0.0)
            if live[c] + load_s > fair[c] * 1.2:
                break
            if live[donor] - load_s < 0.8 * fair[donor]:
                break
            moves[s] = c
            live[c] += load_s
            live[donor] -= load_s
            if live[c] >= 0.8 * fair[c]:
                break
    return moves, decision


# --------------------------------------------------------------------------- runtime (root)
def poll_loads(rest_urls, capacity, prev=None):
    """Interroge /load de chaque contrôleur. `rest_urls[cid]` = 'http://ip:port'.

    Les compteurs Packet-In renvoyés sont CUMULÉS : on en dérive un débit sur
    l'intervalle écoulé (delta / durée). Une moyenne depuis le démarrage décroîtrait
    mécaniquement et masquerait la charge courante après un rééquilibrage.
    Renvoie (loads, switch_ctrl, switch_load, state) ; repasser `state` au tour suivant.
    """
    import json
    import time
    import urllib.request

    prev = prev or {}
    now = time.time()
    loads, switch_ctrl, switch_load, state = {}, {}, {}, {}

    for cid, url in rest_urls.items():
        with urllib.request.urlopen(f"{url}/load", timeout=2) as r:
            d = json.loads(r.read().decode())
        total = float(d.get("total_packetin", 0))
        per_sw = {int(k): float(v) for k, v in d.get("packetin", {}).items()}
        state[cid] = {"t": now, "total": total, "sw": per_sw}

        p = prev.get(cid)
        dt = (now - p["t"]) if p else max(1e-3, float(d.get("uptime", 1)))
        dt = max(1e-3, dt)
        base_total = p["total"] if p else 0.0
        # Un compteur qui recule (contrôleur relancé) : on repart du cumul courant.
        delta = total - base_total if total >= base_total else total

        loads[cid] = {
            "packetin_rate_total": delta / dt,
            "flows_total": float(sum(d.get("flows", {}).values())),
        }
        for sw, count in per_sw.items():
            switch_ctrl[sw] = cid
            base_sw = (p or {}).get("sw", {}).get(sw, 0.0)
            d_sw = count - base_sw if count >= base_sw else count
            switch_load[sw] = d_sw / dt
    return loads, switch_ctrl, switch_load, state


def apply_moves(moves, controller_ep):
    """Réaffecte via ovs-vsctl. `controller_ep[cid]` = 'tcp:127.0.0.1:6653'. Root requis."""
    import subprocess
    for switch, cid in moves.items():
        subprocess.run(["ovs-vsctl", "set-controller", f"s{switch}", controller_ep[cid]],
                       check=False)
    return moves


def main():  # pragma: no cover - boucle temps réel, exécutée dans le conteneur
    import argparse
    import time

    p = argparse.ArgumentParser(description="Rééquilibreur SDN_CTRLML-Edge (émulation)")
    p.add_argument("--controllers", type=int, default=3)
    p.add_argument("--base-of-port", type=int, default=6653)
    p.add_argument("--base-rest-port", type=int, default=8080)
    p.add_argument("--interval", type=float, default=3.0)
    p.add_argument("--rounds", type=int, default=20)
    args = p.parse_args()

    # Capacités hétérogènes Cloud/Fog/Edge (le récit du mémoire).
    tiers = [6000.0, 3500.0, 1800.0]
    capacity = {c: tiers[c % 3] for c in range(args.controllers)}
    rest = {c: f"http://127.0.0.1:{args.base_rest_port + c}" for c in range(args.controllers)}
    ep = {c: f"tcp:127.0.0.1:{args.base_of_port + c}" for c in range(args.controllers)}

    state = None
    for rnd in range(args.rounds):
        try:
            loads, sw_ctrl, sw_load, state = poll_loads(rest, capacity, state)
        except Exception as e:
            log.info(f"[{rnd}] lecture impossible: {e}"); time.sleep(args.interval); continue
        moves, _decision = plan_reassignments(loads, sw_ctrl, sw_load, capacity)
        if moves:
            apply_moves(moves, ep)
            log.info(f"[{rnd}] rééquilibrage: {moves}")
        else:
            log.info(f"[{rnd}] équilibré, aucune migration")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
