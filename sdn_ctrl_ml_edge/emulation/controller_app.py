"""Contrôleur SDN (os-ken, OpenFlow 1.3) du PoC d'émulation.

os-ken = fork maintenu de Ryu (même API, Python moderne ; Ryu ne s'installe plus sur
Python 3.10+). Rôle : commutateur apprenant L2 (pour que le trafic passe) + comptage des
Packet-In par switch, exposé en REST sur /load via un petit serveur HTTP embarqué
(os-ken ne fournit pas le WSGI de Ryu). Le rééquilibreur lit /load, classe les contrôleurs
avec le VRAI clustering du mémoire, et réaffecte les switches surchargés.

Lancé par run_controller.py, une instance par contrôleur (ports OpenFlow/REST distincts).
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import (CONFIG_DISPATCHER, DEAD_DISPATCHER,
                                        MAIN_DISPATCHER, set_ev_cls)
from os_ken.lib.packet import ethernet, ether_types, packet
from os_ken.ofproto import ofproto_v1_3

# Les règles apprises expirent au bout de ce délai, ACTIVES OU NON (hard timeout).
# Un idle_timeout ne suffit pas : un flux continu (ping toutes les 0,3 s) ne devient
# jamais inactif, la règle ne s'efface donc jamais et la charge du plan de contrôle
# retombe à zéro après apprentissage. Le hard timeout force une resignalisation
# périodique : le taux de Packet-In reflète alors le taux d'établissement de flux,
# qui est précisément la charge de contrôle que le mémoire cherche à équilibrer.
HARD_TIMEOUT = 2


class LearnCount13(app_manager.OSKenApp):
    """Apprentissage L2 + compteurs de charge par datapath (switch)."""
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.packetin = {}      # dpid -> nb de Packet-In (proxy de charge du plan de contrôle)
        self.flows = {}         # dpid -> nb de règles installées
        self.connected = set()  # dpid ACTUELLEMENT rattachés (après un set-controller, ça change)
        self.t0 = time.time()
        self._serve_load(int(os.environ.get("LOAD_REST_PORT", "8080")))

    # ---- REST /load (serveur HTTP embarqué, sans dépendance WSGI) ----
    def _serve_load(self, port):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):        # silencieux
                pass

            def do_GET(self):
                if self.path != "/load":
                    self.send_response(404); self.end_headers(); return
                dt = max(1e-3, time.time() - app.t0)
                live = sorted(app.connected)
                body = {
                    "uptime": dt,
                    "switches": live,
                    "packetin": {str(k): app.packetin.get(k, 0) for k in live},
                    "flows": {str(k): app.flows.get(k, 0) for k in live},
                    "packetin_rate": {str(k): app.packetin.get(k, 0) / dt for k in live},
                    "total_packetin": sum(app.packetin.get(k, 0) for k in live),
                }
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    # ---- suivi des rattachements switch<->contrôleur ----
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change(self, ev):
        dp = ev.datapath
        if dp is None:
            return
        if ev.state == MAIN_DISPATCHER:
            self.connected.add(dp.id)
        elif ev.state == DEAD_DISPATCHER:
            self.connected.discard(dp.id)

    # ---- OpenFlow ----
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features(self, ev):
        dp = ev.msg.datapath
        ofp, psr = dp.ofproto, dp.ofproto_parser
        self.packetin.setdefault(dp.id, 0)
        self.flows.setdefault(dp.id, 0)
        # table-miss : tout paquet inconnu remonte au contrôleur (génère les Packet-In)
        self._add(dp, 0, psr.OFPMatch(),
                  [psr.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])

    def _add(self, dp, priority, match, actions, hard=0):
        ofp, psr = dp.ofproto, dp.ofproto_parser
        inst = [psr.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(psr.OFPFlowMod(datapath=dp, priority=priority, hard_timeout=hard,
                                   match=match, instructions=inst))
        self.flows[dp.id] = self.flows.get(dp.id, 0) + 1

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp, psr = dp.ofproto, dp.ofproto_parser
        in_port = msg.match["in_port"]
        self.packetin[dp.id] = self.packetin.get(dp.id, 0) + 1

        eth = packet.Packet(msg.data).get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        self.mac_to_port.setdefault(dp.id, {})[eth.src] = in_port
        out_port = self.mac_to_port[dp.id].get(eth.dst, ofp.OFPP_FLOOD)
        actions = [psr.OFPActionOutput(out_port)]
        if out_port != ofp.OFPP_FLOOD:                      # règle apprise, mais EXPIRABLE
            self._add(dp, 1, psr.OFPMatch(eth_dst=eth.dst), actions, hard=HARD_TIMEOUT)
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(psr.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                     in_port=in_port, actions=actions, data=data))
