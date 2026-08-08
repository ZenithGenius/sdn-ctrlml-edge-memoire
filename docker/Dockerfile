# Image du PoC : Mininet + Open vSwitch (réel OpenFlow) + os-ken + le code du mémoire.
#
# Base : iwaseyusuke/mininet (Python 3.10, Mininet + OVS prêts). On N'utilise PAS
# containernet/containernet:latest car son Python 3.6 ne fait tourner aucun contrôleur
# maintenu (Ryu exige 3.7+, os-ken 3.8+). os-ken = fork maintenu de Ryu, pur Python,
# pas de Java. Construction et lancement : docker/docker-compose.yml (ou run.sh).
FROM iwaseyusuke/mininet:latest

# os-ken (contrôleur) + scikit-learn/numpy (le VRAI clustering du mémoire),
# et pandas/matplotlib pour que le simulateur et la suite de tests tournent aussi
# dans l'image : sous Windows, cela évite d'installer Python sur la machine.
# MLflow est volontairement omis (utile au seul plan d'expériences, très lourd).
# L'image mininet n'embarque pas pip : on l'installe via apt d'abord.
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir os-ken \
 && grep -v '^mlflow' /tmp/requirements.txt | pip3 install --no-cache-dir -r /dev/stdin

WORKDIR /sdn
COPY sdn_ctrl_ml_edge /sdn/sdn_ctrl_ml_edge
COPY tests /sdn/tests
COPY docker/entrypoint.sh /entrypoint.sh
# Retrait des retours chariot : un clone sous Windows peut livrer le script en
# CRLF, et le shebang serait alors lu comme « bash\r » (script inexécutable).
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh
ENV PYTHONPATH=/sdn
ENV PYTHONUNBUFFERED=1

# Remplace l'entrypoint de l'image de base, qui ouvre un shell et ignore la commande.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "-m", "sdn_ctrl_ml_edge.emulation.run_poc", "--controllers", "3", "--switches", "6"]
