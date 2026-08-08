"""Orchestrateur du PoC d'émulation (à exécuter EN ROOT, dans le conteneur Containernet).

Enchaîne, de bout en bout, la démonstration réelle sur switches OVS :
  1. lance K contrôleurs Ryu (controller_app.py) sur des ports distincts ;
  2. construit une topologie Containernet (switches OVS + hôtes) ;
  3. déséquilibre volontairement : TOUS les switches sur le contrôleur 0 ;
  4. génère du trafic (les hôtes se pinguent) -> Packet-In -> charge réelle ;
  5. à chaque tour : lit la charge REST, applique le VRAI algorithme (rebalancer),
     réaffecte les switches surchargés, et mesure l'équité de Jain.

But : montrer, sur du vrai OpenFlow, que l'équité passe de « tout sur c0 » à répartie.
Ne tourne pas dans ce bac à sable (OVS/root indisponibles) : lancé par run.sh sur l'hôte.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
import warnings

import numpy as np

# K-means avertit quand des contrôleurs ont des vecteurs identiques (tous à charge
# nulle au démarrage) : sans intérêt ici, et illisible au milieu du tableau.
warnings.filterwarnings("ignore", message=".*Number of distinct clusters.*")

from .rebalancer import apply_moves, plan_reassignments, poll_loads, smooth_loads

TIERS = [6000.0, 3500.0, 1800.0]                          # capacités Cloud/Fog/Edge

log = logging.getLogger(__name__)


def jain(values):
    """Indice d'équité de Jain, ou None si la charge totale est négligeable.

    Renvoyer 1.0 quand tout est à zéro serait trompeur : « parfaitement équilibré »
    alors qu'il n'y a simplement rien à mesurer. On affiche alors « n/a ».
    """
    a = np.asarray(list(values), dtype=float)
    if a.sum() < 1e-6:
        return None
    d = len(a) * np.sum(a ** 2)
    return float(np.sum(a) ** 2 / d) if d > 0 else None


def jain_weighted(rates, caps):
    """Équité PONDÉRÉE par la capacité : 1 = charge proportionnelle aux capacités.

    Avec des contrôleurs hétérogènes, l'indice brut est trompeur : il vaudrait 1 si
    tous portaient la MÊME charge, ce qui saturerait le petit contrôleur Edge tout en
    laissant le Cloud oisif. La cible correcte est « à chacun selon sa capacité ».
    """
    return jain([r / max(1.0, c) for r, c in zip(rates, caps)])


def start_controllers(n, base_of, base_rest):
    """Lance n contrôleurs os-ken (chacun un processus, ports OpenFlow/REST distincts)."""
    procs = []
    for i in range(n):
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "sdn_ctrl_ml_edge.emulation.run_controller",
             str(base_of + i), str(base_rest + i)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return procs


def build_net(n_ctrl, n_sw, hps, base_of):
    # Containernet est un sur-ensemble de Mininet ; on prend Containernet s'il est là.
    try:
        from mininet.net import Containernet as Net
    except ImportError:
        from mininet.net import Mininet as Net
    from mininet.node import OVSSwitch, RemoteController
    from mininet.link import TCLink

    net = Net(switch=OVSSwitch, link=TCLink)
    ctrls = [net.addController(f"c{i}", controller=RemoteController,
                               ip="127.0.0.1", port=base_of + i) for i in range(n_ctrl)]
    switches, hosts, hid = [], [], 1
    for j in range(1, n_sw + 1):
        s = net.addSwitch(f"s{j}", protocols="OpenFlow13", dpid=f"{j:016x}")
        switches.append(s)
    for a, b in zip(switches, switches[1:]):              # dorsale : switches en chaîne
        net.addLink(a, b, cls=TCLink, delay="2ms")
    for s in switches:
        for _ in range(hps):
            h = net.addHost(f"h{hid}", ip=f"10.0.0.{hid}/24")
            net.addLink(h, s)
            hosts.append(h); hid += 1
    net.build()
    for c in ctrls:
        c.start()
    for s in switches:                                    # déséquilibre initial : tout sur c0
        s.start([ctrls[0]])
    return net, switches, hosts


def main():
    p = argparse.ArgumentParser(description="PoC émulation SDN_CTRLML-Edge (Containernet)")
    p.add_argument("--controllers", type=int, default=3)
    p.add_argument("--switches", type=int, default=6)
    p.add_argument("--hosts-per-switch", type=int, default=2)
    p.add_argument("--rounds", type=int, default=12)
    p.add_argument("--interval", type=float, default=3.0)
    p.add_argument("--base-of-port", type=int, default=6653)
    p.add_argument("--base-rest-port", type=int, default=8080)
    p.add_argument("--no-rebalance", action="store_true",
                   help="baseline : ne PAS rééquilibrer (montre le déséquilibre ONOS-like)")
    p.add_argument("--traffic", choices=["constant", "pics"], default="constant",
                   help="profil de charge : constant, ou pics (rafales périodiques)")
    p.add_argument("--alpha", type=float, default=0.4,
                   help="lissage EMA des charges avant décision (0 = pas de lissage)")
    p.add_argument("--no-ema", action="store_true",
                   help="décider sur la mesure brute (montre l'apport de l'EMA)")
    p.add_argument("--trace-out", default=None,
                   help="écrit la trace de l'exécution en JSON (pour make flowviz --from)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        stream=sys.stdout)

    from mininet.log import setLogLevel
    setLogLevel("warning")

    capacity = {c: TIERS[c % 3] for c in range(args.controllers)}
    rest = {c: f"http://127.0.0.1:{args.base_rest_port + c}" for c in range(args.controllers)}
    ep = {c: f"tcp:127.0.0.1:{args.base_of_port + c}" for c in range(args.controllers)}

    log.info(">> démarrage des contrôleurs os-ken…")
    procs = start_controllers(args.controllers, args.base_of_port, args.base_rest_port)
    time.sleep(4)

    log.info(">> construction de la topologie (switches OVS + hôtes)…")
    net, switches, hosts = build_net(args.controllers, args.switches,
                                     args.hosts_per_switch, args.base_of_port)
    try:
        # Trafic de fond : chaque hôte pingue PLUSIEURS destinations éloignées.
        # La diversité des paires (donc des flux) rend le taux d'établissement de
        # flux, et par conséquent la charge de contrôle, stable et mesurable.
        n_h = len(hosts)
        for i, h in enumerate(hosts):
            for k in (1, n_h // 3, 2 * n_h // 3):
                tgt = hosts[(i + max(1, k)) % n_h]
                if tgt is not h:
                    h.cmd(f"ping -i 0.4 {tgt.IP()} > /dev/null 2>&1 &")

        if args.traffic == "pics":
            # Rafales périodiques : toutes les 12 s, la moitié des hôtes balaie une
            # plage d'adresses (chaque destination = un flux neuf, donc un Packet-In).
            # Éprouve la réaction de l'algorithme à une charge non stationnaire.
            burst = " ".join(f"10.0.0.{j}" for j in range(1, min(n_h, 40) + 1))
            for h in hosts[::2]:
                h.cmd("bash -c 'while true; do for ip in " + burst +
                      "; do ping -c1 -W1 $ip >/dev/null 2>&1 & done; sleep 12; done' &")

        cids = sorted(capacity)
        mode = "SANS rééquilibrage (baseline)" if args.no_rebalance \
            else "AVEC rééquilibrage (SDN_CTRLML-Edge)"
        tier_name = {6000.0: "Cloud", 3500.0: "Fog", 1800.0: "Edge"}

        # --- en-tête du tableau : une colonne par contrôleur -----------------
        col = 11
        sep = "+------+-------+-------+" + "+".join("-" * col for _ in cids) + \
            "+-------+------------+"
        head1 = f"| {'tour':<4} | {'Jain':<5} | {'Jain*':<5} |" + \
            "".join(f"{('c' + str(c)):^{col}}" + "|" for c in cids) + \
            f" {'migr.':<5} | {'switches':<10} |"
        head2 = f"| {'':<4} | {'brut':<5} | {'pond.':<5} |" + \
            "".join(f"{tier_name.get(capacity[c], '?'):^{col}}" + "|" for c in cids) + \
            f" {'':<5} | {'déplacés':<10} |"

        log.info(f"\n>> Mode : {mode}")
        log.info(f">> Réseau : {args.controllers} contrôleurs, {args.switches} switches, "
              f"{len(hosts)} hôtes · trafic {args.traffic}")
        log.info(">> Charge = Packet-In/s par contrôleur (mesurée sur l'intervalle)")
        log.info(">> Jain* = équité pondérée par la capacité (1 = « à chacun selon sa capacité »)\n")
        log.info(sep); log.info(head1); log.info(head2); log.info(sep)

        # Trace destinée à la visualisation : on enregistre l'état réel mesuré à
        # chaque cycle, pour rejouer ensuite l'émulation à l'écran sans la relancer.
        tier_of = {6000.0: "cloud", 3500.0: "fog", 1800.0: "edge"}
        trace = {"meta": {"controllers": args.controllers, "switches": args.switches,
                          "hosts_per_switch": args.hosts_per_switch,
                          "n_hosts": len(hosts),
                          "traffic": args.traffic, "rebalance": not args.no_rebalance,
                          "capacity": {str(c): capacity[c] for c in cids},
                          # Poids relatifs Cloud/Fog/Edge pour la part équitable —
                          # pas des plafonds CPU qui couperaient le trafic.
                          "tiers": {str(c): tier_of.get(capacity[c], "edge") for c in cids},
                          "balancer": "SDN_CTRLML-Edge (K-means++ multi-métriques + délestage/remplissage)"},
                 "cycles": []}
        assign = {}                       # dernier rattachement connu, switch -> contrôleur

        state, ema = None, None
        for rnd in range(args.rounds):
            time.sleep(args.interval)
            try:
                loads, sw_ctrl, sw_load, state = poll_loads(rest, capacity, state)
            except Exception as e:
                log.info(f"| {rnd:<4} | lecture impossible: {e}"); continue
            rates = [loads[c]["packetin_rate_total"] for c in cids]
            j = jain(rates)
            jw = jain_weighted(rates, [capacity[c] for c in cids])

            # La DÉCISION se prend sur les charges lissées (EMA), pas sur la mesure
            # brute : le tableau affiche le réel, l'algorithme ignore les transitoires.
            ema = smooth_loads(loads, ema, 1.0 if args.no_ema else args.alpha)
            moves = {}
            decision = {}
            if not args.no_rebalance:
                smoothed = {c: {"packetin_rate_total": ema[c],
                                "flows_total": loads[c]["flows_total"]} for c in loads}
                moves, decision = plan_reassignments(smoothed, sw_ctrl, sw_load, capacity,
                                                     loads_raw=loads)
                if moves:
                    apply_moves(moves, ep)
            assign.update(sw_ctrl)                       # rattachements observés
            assign.update(moves)                         # puis ceux qu'on vient d'appliquer
            cycle = {
                "round": rnd,
                "load": {str(c): round(loads[c]["packetin_rate_total"], 2) for c in cids},
                "assign": {str(s): int(c) for s, c in sorted(assign.items())},
                "moves": {str(s): int(c) for s, c in sorted(moves.items())},
                "jain": None if j is None else round(j, 4),
                "jain_w": None if jw is None else round(jw, 4),
            }
            if decision:
                cycle["cluster"] = decision["cluster"]
                cycle["profile"] = decision["profile"]
                cycle["silhouette"] = decision["silhouette"]
                cycle["k"] = decision["k"]
            trace["cycles"].append(cycle)

            moved = ",".join(f"s{s}" for s in sorted(moves)) or "-"
            fmt = lambda x: f"{x:5.3f}" if x is not None else " n/a "  # noqa: E731
            row = f"| {rnd:<4} | {fmt(j)} | {fmt(jw)} |" + \
                "".join(f"{r:^{col}.1f}" + "|" for r in rates) + \
                f" {len(moves):<5} | {moved[:10]:<10} |"
            log.info(row)
        log.info(sep)

        if args.trace_out:
            import json
            from pathlib import Path
            Path(args.trace_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.trace_out).write_text(json.dumps(trace), encoding="utf-8")
            log.info(f"\n>> Trace écrite : {args.trace_out}")

        log.info("\n>> Lecture : l'indice de Jain vaut 1 quand la charge est parfaitement")
        log.info(">> répartie. Sans rééquilibrage elle reste concentrée sur c0 (Jain bas) ;")
        log.info(">> avec l'algorithme, les switches migrent vers les contrôleurs sains.")
    finally:
        net.stop()
        for pr in procs:
            pr.terminate()


if __name__ == "__main__":
    sys.exit(main())
