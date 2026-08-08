"""Visualisation de la topologie SDN à partir d'une trace d'émulation réelle.

S'appuie sur des bibliothèques établies plutôt que sur un rendu maison :
  - networkx : modèle de graphe (nœuds, liens, attributs) ;
  - pyvis    : rendu interactif, qui encapsule vis.js (déplacement des nœuds,
               zoom, physique) et produit une page HTML autonome ;
  - matplotlib : rendu statique, pour les figures insérées dans le mémoire.

  python -m sdn_ctrl_ml_edge.topoviz --from reports/emulation/trace.json
  python -m sdn_ctrl_ml_edge.topoviz --from ... --static reports/figures/topo.png

La trace provient de `run_poc.py --trace-out` : les charges et les rattachements
affichés sont ceux mesurés sur les commutateurs Open vSwitch.
"""
from __future__ import annotations

import argparse
import logging
import sys
import json
from pathlib import Path

import networkx as nx

TIER = {6000.0: ("cloud", "Cloud"), 3500.0: ("fog", "Fog"), 1800.0: ("edge", "Edge")}
# Palette du mémoire (cohérente avec les figures du chapitre III).
NAVY = "#1F4E79"
PALETTE = ["#5B8FF9", "#61DDAA", "#F6BD16", "#7262FD", "#78D3F8", "#F08BB4"]

log = logging.getLogger(__name__)


def load_trace(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_graph(trace: dict, cycle: int = -1) -> nx.Graph:
    """Construit le graphe de l'infrastructure à un cycle donné.

    Les attributs portés par les nœuds et les liens (charge, palier, domaine) sont
    ensuite exploités indifféremment par le rendu interactif ou statique.
    """
    meta = trace["meta"]
    caps = {int(k): v for k, v in meta["capacity"].items()}
    cyc = trace["cycles"][cycle]
    load = {int(k): v for k, v in cyc["load"].items()}
    assign = {int(k): v for k, v in cyc["assign"].items()}
    moves = {int(k): v for k, v in cyc.get("moves", {}).items()}

    total = sum(load.values()) or 1.0
    cap_tot = sum(caps.values()) or 1.0

    g = nx.Graph()
    for c in sorted(caps):
        part = total * caps[c] / cap_tot              # charge attendue si équilibré
        ratio = load.get(c, 0.0) / part if part else 0.0
        g.add_node(f"c{c}", kind="controller", cid=c, capacity=caps[c],
                   tier=TIER.get(caps[c], ("edge", "Edge"))[0],
                   tier_label=TIER.get(caps[c], ("edge", "Edge"))[1],
                   load=load.get(c, 0.0), ratio=ratio, saturated=ratio > 1.5)

    n_sw = meta["switches"]
    for s in range(1, n_sw + 1):
        g.add_node(f"s{s}", kind="switch", sid=s)
    for s in range(1, n_sw):                          # dorsale du plan de données
        g.add_edge(f"s{s}", f"s{s+1}", kind="data")
    for s, c in assign.items():                       # interface Sud (OpenFlow)
        if f"s{s}" in g and f"c{c}" in g:
            g.add_edge(f"s{s}", f"c{c}", kind="south", domain=c, moved=s in moves)

    ctrl = [f"c{c}" for c in sorted(caps)]
    for a, b in zip(ctrl, ctrl[1:]):                  # interface Est-Ouest
        g.add_edge(a, b, kind="eastwest")
    g.graph.update(cycle=cyc.get("round", 0), jain=cyc.get("jain"),
                   traffic=meta.get("traffic", "constant"), n_cycles=len(trace["cycles"]))
    return g


def _load_color(ratio: float) -> str:
    """Vert (peu chargé) vers rouge (saturé), par rapport à la part équitable."""
    stops = [(0.0, (46, 139, 87)), (0.8, (224, 168, 0)),
             (1.2, (232, 89, 12)), (1.8, (192, 57, 43))]
    r = max(0.0, min(ratio, 1.8))
    for (x0, c0), (x1, c1) in zip(stops, stops[1:]):
        if x0 <= r <= x1:
            t = (r - x0) / (x1 - x0 or 1)
            return "#%02X%02X%02X" % tuple(int(a + (b - a) * t) for a, b in zip(c0, c1))
    return "#C0392B"


def render_interactive(g: nx.Graph, out: str) -> str:
    """Page interactive via pyvis (vis.js) : nœuds déplaçables, zoom, info-bulles."""
    from pyvis.network import Network

    net = Network(height="640px", width="100%", bgcolor="#ffffff",
                  font_color="#2b3446", directed=False,
                  cdn_resources="in_line",     # tout est embarqué : page autonome
                  notebook=False)
    net.toggle_physics(True)

    for n, d in g.nodes(data=True):
        if d["kind"] == "controller":
            net.add_node(
                n, label=n, shape="dot", size=18 + d["capacity"] / 400,
                color={"background": _load_color(d["ratio"]),
                       "border": "#C0392B" if d["saturated"] else NAVY,
                       "highlight": {"background": _load_color(d["ratio"]), "border": NAVY}},
                borderWidth=4 if d["saturated"] else 2,
                level=0, group="controleur",
                title=(f"Contrôleur {n} ({d['tier_label']})\n"
                       f"Capacité : {d['capacity']:.0f} req/s\n"
                       f"Charge mesurée : {d['load']:.1f} Packet-In/s\n"
                       f"Part de charge : {d['ratio']:.2f} fois sa part équitable"
                       + ("\nÉTAT : saturé" if d["saturated"] else "")))
        else:
            net.add_node(n, label=n, shape="square", size=11,
                         color={"background": "#9AA7BD", "border": "#7c8aa3"},
                         level=1, group="commutateur",
                         title=f"Commutateur OpenFlow {n}")

    for a, b, d in g.edges(data=True):
        if d["kind"] == "south":
            net.add_edge(a, b, width=4 if d.get("moved") else 1.6,
                         color=PALETTE[d["domain"] % len(PALETTE)],
                         dashes=bool(d.get("moved")),
                         title="Rattachement OpenFlow" + (" (vient d'être migré)"
                                                          if d.get("moved") else ""))
        elif d["kind"] == "eastwest":
            net.add_edge(a, b, width=1.6, color="#1F4E79", dashes=True,
                         title="Interface Est-Ouest (coordination entre contrôleurs)")
        else:
            net.add_edge(a, b, width=2, color="#d7dee9", title="Lien du plan de données")

    net.set_options("""{
      "layout": {"hierarchical": {"enabled": true, "direction": "DU",
                 "sortMethod": "directed", "levelSeparation": 220, "nodeSpacing": 110}},
      "physics": {"enabled": false},
      "interaction": {"hover": true, "navigationButtons": true, "tooltipDelay": 120}
    }""")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    net.save_graph(out)
    return out


def render_static(g: nx.Graph, out: str) -> str:
    """Figure statique via networkx + matplotlib, au format des figures du mémoire."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams.update({"font.family": "serif",
                         "font.serif": ["Liberation Serif", "DejaVu Serif"]})
    ctrls = [n for n, d in g.nodes(data=True) if d["kind"] == "controller"]
    sws = [n for n, d in g.nodes(data=True) if d["kind"] == "switch"]
    # Disposition en deux rangées : plan de contrôle au-dessus, plan de données en dessous.
    pos = {n: ((i + 0.5) / len(ctrls), 1.0) for i, n in enumerate(sorted(ctrls))}
    pos.update({n: ((i + 0.5) / len(sws), 0.0) for i, n in enumerate(
        sorted(sws, key=lambda x: int(x[1:])))})

    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    ax.axhspan(0.78, 1.22, color="#F2F6FB", zorder=0)
    ax.axhspan(-0.22, 0.22, color="#F7FAF7", zorder=0)
    ax.text(-0.045, 1.0, "Plan de\ncontrôle", ha="center", va="center",
            fontsize=8.5, style="italic", color="#6b7688")
    ax.text(-0.045, 0.0, "Plan de\ndonnées", ha="center", va="center",
            fontsize=8.5, style="italic", color="#6b7688")

    for a, b, d in g.edges(data=True):
        if d["kind"] == "south":
            nx.draw_networkx_edges(g, pos, [(a, b)], ax=ax,
                                   edge_color=PALETTE[d["domain"] % len(PALETTE)],
                                   width=2.4 if d.get("moved") else 1.0,
                                   style="dashed" if d.get("moved") else "solid",
                                   alpha=0.9 if d.get("moved") else 0.55)
        elif d["kind"] == "eastwest":
            nx.draw_networkx_edges(g, pos, [(a, b)], ax=ax, edge_color=NAVY,
                                   width=1.4, style=(0, (5, 4)), alpha=0.7)
        else:
            nx.draw_networkx_edges(g, pos, [(a, b)], ax=ax, edge_color="#C9D3E0", width=1.8)

    nx.draw_networkx_nodes(g, pos, sws, ax=ax, node_shape="s", node_size=210,
                           node_color="#9AA7BD", edgecolors="#6b7688", linewidths=0.8)
    nx.draw_networkx_nodes(
        g, pos, sorted(ctrls), ax=ax, node_shape="o",
        node_size=[520 + g.nodes[n]["capacity"] / 12 for n in sorted(ctrls)],
        node_color=[_load_color(g.nodes[n]["ratio"]) for n in sorted(ctrls)],
        edgecolors=["#C0392B" if g.nodes[n]["saturated"] else NAVY for n in sorted(ctrls)],
        linewidths=[2.6 if g.nodes[n]["saturated"] else 1.4 for n in sorted(ctrls)])
    nx.draw_networkx_labels(g, pos, {n: n for n in g}, ax=ax, font_size=8,
                            font_family="serif")
    for n in sorted(ctrls):
        ax.annotate(g.nodes[n]["tier_label"], (pos[n][0], pos[n][1] + 0.16),
                    ha="center", fontsize=7.5, color="#6b7688")

    ax.legend(handles=[
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#2E8B57", markersize=10,
               label="contrôleur en deçà de sa part"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#C0392B", markersize=10,
               label="contrôleur saturé"),
        Line2D([0], [0], color=NAVY, linestyle=(0, (5, 4)), label="interface Est-Ouest"),
        Line2D([0], [0], color=PALETTE[0], label="rattachement OpenFlow (domaine)"),
    ], loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=4, frameon=False, fontsize=8)

    ax.set_xlim(-0.09, 1.05); ax.set_ylim(-0.45, 1.3); ax.axis("off")
    fig.tight_layout()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def main() -> None:
    p = argparse.ArgumentParser(
        description="Visualisation de la topologie SDN (networkx + pyvis)")
    p.add_argument("--from", dest="trace", required=True,
                   help="trace JSON produite par run_poc.py --trace-out")
    p.add_argument("--cycle", type=int, default=-1,
                   help="cycle à représenter (-1 = le dernier)")
    p.add_argument("--out", default="reports/topologie.html")
    p.add_argument("--static", default=None,
                   help="produit aussi une image PNG pour le mémoire")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        stream=sys.stdout)

    trace = load_trace(args.trace)
    g = build_graph(trace, args.cycle)
    log.info(f"Graphe : {g.number_of_nodes()} nœuds, {g.number_of_edges()} liens "
          f"(cycle {g.graph['cycle']}, équité {g.graph['jain']})")
    log.info("Écrit :", render_interactive(g, args.out))
    if args.static:
        log.info("Écrit :", render_static(g, args.static))


if __name__ == "__main__":
    main()
