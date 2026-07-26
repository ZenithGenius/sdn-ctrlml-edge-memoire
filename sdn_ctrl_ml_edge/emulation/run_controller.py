"""Lanceur d'un contrôleur os-ken (équivalent de `ryu-manager`, absent d'os-ken pip).

  python -m sdn_ctrl_ml_edge.emulation.run_controller <of_port> <rest_port>

Monkey-patch eventlet, fixe le port d'écoute OpenFlow, puis délègue à
AppManager.run_apps (qui instancie l'app, démarre l'OpenFlowController et la boucle).
"""
import sys

import eventlet
eventlet.monkey_patch()

import os                                       # noqa: E402
from os_ken import cfg                          # noqa: E402
from os_ken.base.app_manager import AppManager  # noqa: E402


def main():
    of_port = sys.argv[1] if len(sys.argv) > 1 else "6653"
    rest_port = sys.argv[2] if len(sys.argv) > 2 else "8080"
    os.environ["LOAD_REST_PORT"] = rest_port
    cfg.CONF(["--ofp-tcp-listen-port", of_port], project="os_ken")
    AppManager.run_apps(["sdn_ctrl_ml_edge.emulation.controller_app"])


if __name__ == "__main__":
    main()
