"""Génère une visualisation autonome du flux (HTML + canvas) pilotée par le vrai simulateur.

  make flowviz            -> reports/flux_sdn.html   (ouvrir dans un navigateur)
  make flowviz RATE=24000

L'animation montre côte à côte le proposé et ONOS statique : des particules (les flux)
circulent des commutateurs vers les contrôleurs. La destination de chaque particule est
tirée selon la part de flux RÉELLE calculée par la stratégie à ce cycle. Donc, sans aucun
trucage, les flux évitent les contrôleurs rouges sous le proposé et s'y entassent sous ONOS.
"""
from __future__ import annotations

import argparse
import logging
import sys
import json

from . import config
from .dataset import build_infrastructure
from .simulate import run


log = logging.getLogger(__name__)

def _profile(t, c):
    lab = t["labels"][c]
    if lab in t["overloaded"]:
        return "surchargé"
    if lab in t["underutilized"]:
        return "sous-utilisé"
    return "normal"


def build_payload(rate: float, n_flows: int, n_ctrl: int, seed: int) -> dict:
    infra = build_infrastructure(config.DEFAULT_TOPOLOGY, n_ctrl, seed)
    pos = {n: infra.graph.nodes[n]["pos"] for n in infra.graph.nodes()}
    ctrl = set(infra.controllers)

    cycles = {}
    for strat in ("edge", "onos"):
        tr: list = []
        run(strategy=strat, arrival_rate=rate, n_flows=n_flows,
            n_controllers=n_ctrl, seed=seed, trace=tr)
        frames = []
        for t in (u for u in tr if u["labels"]):
            frames.append({
                "load": {str(c): round(t["raw_cpu"][c], 4) for c in infra.controllers},
                "share": {str(c): round(t["share"][c], 4) for c in infra.controllers},
                "hot": [c for c in infra.controllers if _profile(t, c) == "surchargé"],
            })
        cycles[strat] = frames

    return {
        "meta": {"rate": rate, "n_flows": n_flows, "n_ctrl": n_ctrl, "seed": seed},
        "nodes": [{"id": n, "x": round(pos[n][0], 4), "y": round(pos[n][1], 4),
                   "ctrl": n in ctrl, "tier": infra.tier.get(n, "edge"),
                   "cap": infra.capacity.get(n, 0),
                   # domaine SDN : contrôleur le plus proche (rattachement Sud/OpenFlow)
                   "home": (None if n in ctrl else infra.nearest_controller(n))}
                  for n in infra.nodes],
        "cycles": cycles,
    }


HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SDN_CTRLML-Edge - Flux en direct</title>
<style>
  :root{--navy:#1F4E79;--ink:#2b3446;--muted:#6b7688;--line:#e3e7ee;}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Segoe UI',system-ui,sans-serif;color:var(--ink);
       background:#f5f7fb;padding:22px 26px}
  h1{color:var(--navy);font-size:1.5rem}
  .sub{color:var(--muted);margin:4px 0 16px;max-width:900px;line-height:1.45}
  .arch{display:flex;align-items:stretch;gap:8px;margin:6px 0 16px;flex-wrap:wrap}
  .plane{background:#fff;border:1px solid var(--line);border-left:4px solid var(--navy);
         border-radius:8px;padding:8px 12px;flex:1;min-width:210px}
  .plane b{color:var(--navy);display:block;font-size:.95rem}
  .plane span{color:var(--muted);font-size:.8rem}
  .plane.ctrl{border-left-color:#E0A800;background:#fffdf6}
  .iface{align-self:center;color:var(--muted);font-size:.74rem;white-space:nowrap;font-weight:600}
  .panels{display:grid;grid-template-columns:1fr 1fr;gap:18px}
  .card{background:#fff;border:1px solid var(--line);border-radius:12px;
        box-shadow:0 2px 10px rgba(31,78,121,.06);overflow:hidden}
  .card h2{font-size:1.05rem;padding:12px 16px 0}
  .card .badge{font-size:.85rem;padding:2px 16px 10px;font-weight:600}
  .green{color:#2E8B57}.red{color:#C0392B}
  canvas{display:block;width:100%;height:auto;background:#fbfcfe}
  .bar{display:flex;align-items:center;gap:16px;flex-wrap:wrap;
       background:#fff;border:1px solid var(--line);border-radius:12px;
       padding:12px 16px;margin-top:16px}
  button{background:var(--navy);color:#fff;border:0;border-radius:8px;
         padding:8px 16px;font-weight:600;cursor:pointer}
  button.ghost{background:#eef1f6;color:var(--navy)}
  input[type=range]{width:280px}
  .legend{display:flex;gap:18px;flex-wrap:wrap;color:var(--muted);
          font-size:.85rem;margin-top:12px;align-items:center}
  .dot{display:inline-block;width:12px;height:12px;border-radius:50%;
       vertical-align:middle;margin-right:5px}
  .cycle{font-variant-numeric:tabular-nums;font-weight:700;color:var(--navy)}
  .stat{font-variant-numeric:tabular-nums}
</style></head>
<body>
  <h1>SDN_CTRLML-Edge - le flux en direct</h1>
  <p class="sub">Chaque point lumineux est un flux qui rejoint un contrôleur. Sa destination
  suit la part de flux <b>réellement calculée par la stratégie</b> à ce cycle. À gauche, la
  solution proposée écarte les contrôleurs saturés (cercles rouges) ; à droite, ONOS continue
  de les alimenter et ils restent rouges. Même trafic, même réseau.</p>

  <div class="arch">
    <div class="plane"><b>Plan application</b>
      <span>politique d'equilibrage : EMA + K-means++ multi-metriques</span></div>
    <div class="iface">&darr; API Nord</div>
    <div class="plane ctrl"><b>Plan de controle decentralise</b>
      <span>controleurs SDN cooperants &middot; interface Est-Ouest (liens pointilles)</span></div>
    <div class="iface">&darr; API Sud (OpenFlow)</div>
    <div class="plane"><b>Plan de donnees</b>
      <span>commutateurs OpenFlow groupes par domaine (liens colores)</span></div>
  </div>

  <div class="panels">
    <div class="card"><h2>SDN_CTRLML-Edge (propose)</h2>
      <div class="badge green" id="badge-edge"></div>
      <canvas id="cv-edge"></canvas></div>
    <div class="card"><h2>ONOS statique</h2>
      <div class="badge red" id="badge-onos"></div>
      <canvas id="cv-onos"></canvas></div>
  </div>

  <div class="bar">
    <button id="play">Pause</button>
    <button class="ghost" id="restart">Rejouer</button>
    <label>Cycle <span class="cycle" id="cyc">1</span></label>
    <input type="range" id="scrub" min="0" value="0">
    <label>Vitesse <input type="range" id="speed" min="1" max="10" value="5"></label>
    <span class="stat" id="rate"></span>
  </div>
  <div class="legend">
    <span><span class="dot" style="background:#2E8B57"></span>controleur sain</span>
    <span><span class="dot" style="background:#E0A800"></span>charge moyenne</span>
    <span><span class="dot" style="background:#C0392B"></span>sature</span>
    <span><span class="dot" style="border:2px solid #C0392B;background:transparent"></span>surcharge detectee par le clustering</span>
    <span>&#9476;&#9476; interface Est-Ouest (controleurs) &nbsp;&middot;&nbsp; liens colores = domaines (Sud)</span>
    <span>Serveur = controleur Cloud &nbsp; Routeur = Fog &nbsp; Commutateur = Edge</span>
  </div>

<script>
const DATA = __PAYLOAD__;
const NAVY='#1F4E79';
// Icones d'equipement reseau dessinees en SVG (aucune ressource externe).
const ICONS_SVG = {
  cloud: "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    +"<rect x='6' y='5' width='20' height='9' rx='2' fill='#1F4E79'/>"
    +"<rect x='6' y='18' width='20' height='9' rx='2' fill='#1F4E79'/>"
    +"<circle cx='10' cy='9.5' r='1.6' fill='#7FE0A8'/><circle cx='10' cy='22.5' r='1.6' fill='#7FE0A8'/>"
    +"<rect x='14' y='8.5' width='9' height='2' rx='1' fill='#fff'/>"
    +"<rect x='14' y='21.5' width='9' height='2' rx='1' fill='#fff'/></svg>",
  fog: "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    +"<path d='M16 11 V4 M12.5 7 L16 3.5 L19.5 7' stroke='#1F4E79' stroke-width='2.4' fill='none' stroke-linecap='round' stroke-linejoin='round'/>"
    +"<rect x='6' y='12' width='20' height='11' rx='2.5' fill='#1F4E79'/>"
    +"<circle cx='11' cy='17.5' r='1.5' fill='#fff'/><circle cx='16' cy='17.5' r='1.5' fill='#fff'/><circle cx='21' cy='17.5' r='1.5' fill='#fff'/></svg>",
  edge: "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    +"<rect x='4' y='12' width='24' height='9' rx='2' fill='#1F4E79'/>"
    +"<rect x='7' y='15' width='2.5' height='3' fill='#fff'/><rect x='11.5' y='15' width='2.5' height='3' fill='#fff'/>"
    +"<rect x='16' y='15' width='2.5' height='3' fill='#fff'/><rect x='20.5' y='15' width='2.5' height='3' fill='#fff'/></svg>",
  sw: "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'>"
    +"<rect x='3' y='9' width='18' height='7' rx='1.5' fill='#9aa7bd'/>"
    +"<rect x='6' y='11' width='2' height='2.5' fill='#fff'/><rect x='10' y='11' width='2' height='2.5' fill='#fff'/>"
    +"<rect x='14' y='11' width='2' height='2.5' fill='#fff'/></svg>",
};
const IMG = {};
function loadIcons(){
  return Promise.all(Object.entries(ICONS_SVG).map(([k,s]) => new Promise(res => {
    const im = new Image(); im.onload = () => { IMG[k] = im; res(); }; im.onerror = () => res();
    im.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(s);
  })));
}
const NF = Math.min(DATA.cycles.edge.length, DATA.cycles.onos.length);
const W = 560, H = 460, PAD = 28;
const nodeById = {}; DATA.nodes.forEach(n => nodeById[n.id] = n);
const px = n => PAD + n.x * (W - 2*PAD);
const py = n => H - PAD - n.y * (H - 2*PAD);          // y up
const srcIds = DATA.nodes.map(n => n.id);

// --- Architecture SDN decentralisee : domaines (Sud) + maillage Est-Ouest ---
const CTRLS = DATA.nodes.filter(n => n.ctrl);
const ctrlIdx = {}; CTRLS.forEach((c, i) => ctrlIdx[c.id] = i);
const DPAL = ['#5B8FF9','#61DDAA','#F6BD16','#7262FD','#78D3F8',
              '#F08BB4','#9270CA','#F6903D','#008685','#D14A61'];
const domColor = cid => DPAL[(ctrlIdx[cid] || 0) % DPAL.length];
const dist2 = (a, b) => (a.x-b.x)**2 + (a.y-b.y)**2;
const EW = []; const _seen = new Set();               // interface Est-Ouest (mesh de controleurs)
for (const a of CTRLS){
  const near = CTRLS.filter(b => b.id !== a.id).sort((p,q)=>dist2(a,p)-dist2(a,q)).slice(0,2);
  for (const b of near){ const k=[a.id,b.id].sort().join('-');
    if(!_seen.has(k)){ _seen.add(k); EW.push([a,b]); } }
}

function loadColor(v){                                  // 0 vert -> 1 rouge
  const stops = [[0,'#2E8B57'],[.5,'#E0A800'],[.75,'#E8590C'],[1,'#C0392B']];
  let a=stops[0],b=stops[stops.length-1];
  for(let i=0;i<stops.length-1;i++){ if(v>=stops[i][0]&&v<=stops[i+1][0]){a=stops[i];b=stops[i+1];break;} }
  const t=(v-a[0])/((b[0]-a[0])||1);
  const c1=hx(a[1]),c2=hx(b[1]);
  return `rgb(${lerp(c1[0],c2[0],t)|0},${lerp(c1[1],c2[1],t)|0},${lerp(c1[2],c2[2],t)|0})`;
}
const hx=s=>[1,3,5].map(i=>parseInt(s.substr(i,2),16));
const lerp=(a,b,t)=>a+(b-a)*t;

function Panel(canvasId, strat){
  const cv=document.getElementById(canvasId); cv.width=W; cv.height=H;
  const ctx=cv.getContext('2d'); const parts=[];
  function frame(){ return DATA.cycles[strat][state.cyc] || DATA.cycles[strat][0]; }
  function ctrlList(){ return DATA.nodes.filter(n=>n.ctrl); }
  function pickDest(){                                  // tirage pondere par la part de flux reelle
    const f=frame(); const cs=ctrlList(); let tot=0;
    for(const c of cs) tot+=(f.share[c.id]||0);
    if(tot<=0) return cs[(Math.random()*cs.length)|0];
    let r=Math.random()*tot;
    for(const c of cs){ r-=(f.share[c.id]||0); if(r<=0) return c; }
    return cs[cs.length-1];
  }
  function spawn(k){
    for(let i=0;i<k;i++){
      const s=nodeById[srcIds[(Math.random()*srcIds.length)|0]];
      const d=pickDest();
      parts.push({sx:px(s),sy:py(s),dx:px(d),dy:py(d),t:0,
                  v:0.010+Math.random()*0.014, hot:frame().hot.includes(d.id)});
    }
  }
  function draw(){
    const f=frame();
    ctx.clearRect(0,0,W,H);
    for(const n of DATA.nodes){ if(n.ctrl||n.home==null) continue;   // interface Sud : domaines
      const c=nodeById[n.home]; ctx.strokeStyle=domColor(n.home);
      ctx.globalAlpha=0.22; ctx.lineWidth=1;
      ctx.beginPath(); ctx.moveTo(px(n),py(n)); ctx.lineTo(px(c),py(c)); ctx.stroke(); }
    ctx.globalAlpha=1;
    ctx.setLineDash([5,4]); ctx.strokeStyle='rgba(31,78,121,.55)'; ctx.lineWidth=1.6;
    for(const [a,b] of EW){                                   // interface Est-Ouest (controle)
      ctx.beginPath(); ctx.moveTo(px(a),py(a)); ctx.lineTo(px(b),py(b)); ctx.stroke(); }
    ctx.setLineDash([]);
    for(const n of DATA.nodes){ if(n.ctrl) continue;          // commutateurs (equipement reseau)
      if(IMG.sw) ctx.drawImage(IMG.sw, px(n)-8, py(n)-8, 16, 16);
      else { ctx.fillStyle='#9aa7bd'; ctx.beginPath(); ctx.arc(px(n),py(n),2.6,0,7); ctx.fill(); } }
    for(const p of parts){                                     // particules = flux
      const x=p.sx+(p.dx-p.sx)*p.t, y=p.sy+(p.dy-p.sy)*p.t;
      ctx.globalAlpha=0.85; ctx.fillStyle=p.hot?'#C0392B':'#2E86C1';
      ctx.beginPath(); ctx.arc(x,y,2.6,0,7); ctx.fill(); ctx.globalAlpha=1;
    }
    for(const c of ctrlList()){                                // controleurs = equipement
      const load=f.load[c.id]||0, hot=f.hot.includes(c.id);
      const R=13+ (c.cap/650);
      if(hot){ ctx.fillStyle='rgba(192,57,43,.14)';            // halo rouge de saturation
        ctx.beginPath(); ctx.arc(px(c),py(c),R+11,0,7); ctx.fill(); }
      ctx.strokeStyle=hot?'#C0392B':loadColor(load);           // anneau = niveau de charge
      ctx.lineWidth=hot?3.6:3;
      ctx.beginPath(); ctx.arc(px(c),py(c),R+4,0,7); ctx.stroke();
      ctx.fillStyle='#fff'; ctx.strokeStyle=loadColor(load); ctx.lineWidth=1.4;  // carte blanche
      ctx.beginPath(); ctx.arc(px(c),py(c),R,0,7); ctx.fill(); ctx.stroke();
      const icon=IMG[c.tier], s=R*1.55;                        // icone d'equipement
      if(icon) ctx.drawImage(icon, px(c)-s/2, py(c)-s/2, s, s);
      ctx.fillStyle=NAVY; ctx.font='bold 10px sans-serif'; ctx.textAlign='center';
      ctx.fillText('C'+c.id, px(c), py(c)+R+13);
    }
  }
  return {
    step(dt){ if(state.playing){ spawn(Math.round(state.speed)); }
      for(let i=parts.length-1;i>=0;i--){ parts[i].t+=parts[i].v*state.speed*0.6;
        if(parts[i].t>=1) parts.splice(i,1); } if(parts.length>900) parts.splice(0,parts.length-900); },
    draw, frame
  };
}

const state={cyc:0,playing:true,speed:5,acc:0};
const pe=Panel('cv-edge','edge'), po=Panel('cv-onos','onos');
document.getElementById('scrub').max=NF-1;
document.getElementById('rate').textContent=DATA.meta.rate.toLocaleString('fr')+' flux/s - '+DATA.meta.n_ctrl+' controleurs';

function badges(){
  const fe=pe.frame(), fo=po.frame();
  document.getElementById('badge-edge').textContent=fe.hot.length+' surcharge(s) detectee(s) par le clustering -> flux redirige vers les sains';
  document.getElementById('badge-onos').textContent=fo.hot.length+' surcharge(s) - ONOS continue de les alimenter (aucun reequilibrage)';
  document.getElementById('cyc').textContent=(state.cyc+1);
  document.getElementById('scrub').value=state.cyc;
}
let last=performance.now();
function loop(now){ const dt=(now-last)/1000; last=now;
  if(state.playing){ state.acc+=dt; if(state.acc>1.1){ state.acc=0;
    state.cyc=(state.cyc+1)%NF; } }
  pe.step(dt); po.step(dt); pe.draw(); po.draw(); badges();
  requestAnimationFrame(loop);
}
loadIcons().then(() => requestAnimationFrame(loop));

document.getElementById('play').onclick=e=>{ state.playing=!state.playing;
  e.target.textContent=state.playing?'Pause':'Lecture'; };
document.getElementById('restart').onclick=()=>{ state.cyc=0; };
document.getElementById('scrub').oninput=e=>{ state.cyc=+e.target.value; state.playing=false;
  document.getElementById('play').textContent='Lecture'; };
document.getElementById('speed').oninput=e=>{ state.speed=+e.target.value; };
</script>
</body></html>"""




def main() -> None:
    p = argparse.ArgumentParser(description="Visualisation du flux SDN_CTRLML-Edge")
    p.add_argument("--from-emulation", dest="from_emu", default=None,
                   help="trace JSON produite par run_poc.py --trace-out")
    p.add_argument("--rate", type=float, default=16000.0)
    p.add_argument("--n-flows", type=int, default=6000)
    p.add_argument("--n-ctrl", type=int, default=config.N_CONTROLLERS)
    p.add_argument("--seed", type=int, default=config.RANDOM_SEED)
    p.add_argument("--out", default=str(config.REPORTS_DIR / "flux_sdn.html"))
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        stream=sys.stdout)

    if args.from_emu:
        # Le rendu d'une trace d'émulation est confié à topoviz (networkx + pyvis),
        # bibliothèques éprouvées, plutôt qu'à un moteur de dessin maison.
        raise SystemExit(
            "Pour une trace d'émulation, utilisez :\n"
            "  python -m sdn_ctrl_ml_edge.topoviz --from <trace.json>")
    payload = build_payload(args.rate, args.n_flows, args.n_ctrl, args.seed)
    template = HTML
    html = template.replace("__PAYLOAD__", json.dumps(payload, separators=(",", ":")))
    from pathlib import Path
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(html, encoding="utf-8")
    log.info(f"Écrit : {args.out}  ({len(html)//1024} Ko, {payload['meta']['n_ctrl']} contrôleurs, "
          f"{len(payload['cycles']['edge'])} cycles)")


if __name__ == "__main__":
    main()
