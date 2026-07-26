# Image du PoC : Mininet + Open vSwitch (réel OpenFlow) + os-ken + le code du mémoire.
#
# Base : iwaseyusuke/mininet (Python 3.10, Mininet + OVS prêts). On N'utilise PAS
# containernet/containernet:latest car son Python 3.6 ne fait tourner aucun contrôleur
# maintenu (Ryu exige 3.7+, os-ken 3.8+). os-ken = fork maintenu de Ryu, pur Python,
# pas de Java. La construction et le lancement passent par run.sh.
FROM iwaseyusuke/mininet:latest

# os-ken (contrôleur) + scikit-learn/numpy (le VRAI clustering du mémoire).
# L'image mininet n'embarque pas pip : on l'installe via apt d'abord.
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip \
 && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir os-ken "scikit-learn>=1.0" numpy

WORKDIR /sdn
COPY sdn_ctrl_ml_edge /sdn/sdn_ctrl_ml_edge
ENV PYTHONPATH=/sdn
ENV PYTHONUNBUFFERED=1

CMD ["python3", "-m", "sdn_ctrl_ml_edge.emulation.run_poc", "--controllers", "3", "--switches", "6"]
