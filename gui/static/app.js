const NODES = [
  {key:"camera", label:"Camera",  ip:"192.168.1.250"},
  {key:"ue",     label:"UE",      ip:"10.230.4.39"},
  {key:"ru",     label:"O-RU",    ip:"192.168.9.20"},
  {key:"du",     label:"DU",      ip:"10.230.4.26"},
  {key:"cu",     label:"CU",      ip:"10.230.4.33"},
  {key:"core",   label:"CN5G",    ip:"local"},
];
const LINK_LABELS = ["eth","5G","FH","F1","N3"];
const CONTROLS = [
  {key:"core",    label:"CN5G"},
  {key:"cu",      label:"CU"},
  {key:"ptp",     label:"PTP"},
  {key:"du",      label:"DU"},
  {key:"ue_reg",  label:"UE reg"},
  {key:"ue_data", label:"Data"},
  {key:"stream",  label:"Stream"},
];
let last = {};
let srvOff = 0;                       // server clock minus browser clock, so timers match the backend
const ok = (st,k) => { const v=st[k]; return !!v && (v.ready!==undefined ? v.ready : v.up); };

function drawTopo(st){
  const svg=document.getElementById("topo");
  const W=980,n=NODES.length,bw=120,bh=52,gap=(W-n*bw)/(n-1),y=8;
  let s="";
  for(let i=0;i<n-1;i++){
    const x1=i*(bw+gap)+bw,x2=(i+1)*(bw+gap),yc=y+bh/2;
    const up=ok(st,NODES[i].key)&&ok(st,NODES[i+1].key);
    s+=`<line class="link ${up?'up':'down'}" x1="${x1}" y1="${yc}" x2="${x2}" y2="${yc}"/>`;
    s+=`<text class="link-label" x="${(x1+x2)/2}" y="${yc-7}" text-anchor="middle">${LINK_LABELS[i]}</text>`;
  }
  NODES.forEach((nd,i)=>{
    const x=i*(bw+gap),v=st[nd.key]||{},up=ok(st,nd.key),cls=up?'up':(v.up||v.started_at)?'warn':'down';
    let extra="";
    if(nd.key==="du"){const p=st.ptp||{}; const c=p.locked?"var(--up)":p.up?"var(--warn)":"var(--down)";
      extra=`<text x="${x+bw/2}" y="${y+bh+15}" text-anchor="middle" style="font-size:11px;fill:${c};font-family:var(--mono)">PTP ${p.locked?'lock':p.up?'sync':'off'}</text>`;}
    s+=`<g class="node ${cls}">
      <rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="8"/>
      <text x="${x+bw/2}" y="${y+22}" text-anchor="middle">${nd.label}</text>
      <text class="ip" x="${x+bw/2}" y="${y+38}" text-anchor="middle">${nd.ip}</text>${extra}
    </g>`;
  });
  svg.innerHTML=s;
}

function fmt(sec){sec=Math.max(0,Math.round(sec));return String(Math.floor(sec/60)).padStart(2,"0")+":"+String(sec%60).padStart(2,"0");}
function timerHtml(s){
  if(s.started_at&&s.ready_at) return `<span class="timer ready" data-s="${s.started_at}" data-r="${s.ready_at}"></span>`;
  if(s.started_at)             return `<span class="timer starting" data-s="${s.started_at}"></span>`;
  if(s.ready)                  return `<span class="timer ready">ready</span>`;
  return `<span class="timer"></span>`;
}
function tick(){                      // runs several times a second; no server call needed
  const now=(Date.now()+srvOff)/1000;
  document.querySelectorAll(".timer[data-s]").forEach(el=>{
    const st=+el.dataset.s, r=el.dataset.r?+el.dataset.r:null;
    el.textContent = r ? `ready in ${fmt(r-st)}` : `starting ${fmt(now-st)}`;
  });
}
// which component to start next = the first one in the order that is not ready yet
function nextBar(st){
  for(let i=0;i<CONTROLS.length;i++){
    const c=CONTROLS[i], s=st[c.key]||{};
    if(s.ready) continue;
    if(s.started_at||s.up)
      return `<div class="nextbar wait">Waiting for <b>${c.label}</b> to be ready &nbsp;${s.started_at?`<span class="timer starting" data-s="${s.started_at}"></span>`:""}<span class="why">${s.detail||""}</span></div>`;
    const prev=i>0?CONTROLS[i-1].label+" is ready - ":"";
    return `<div class="nextbar go">${prev}start <b>${c.label}</b> next</div>`;
  }
  return `<div class="nextbar done">All components are ready</div>`;
}

function drawComponents(st){
  const box=document.getElementById("components");
  box.innerHTML=nextBar(st)+CONTROLS.map(c=>{
    const s=st[c.key]||{};
    const cls=s.ready?"up":(s.up||s.started_at)?"warn":""; const detail=s.detail||"-";
    const duBlocked=(c.key==="du" && !(st.ptp&&st.ptp.locked));
    const dataBlocked=(c.key==="ue_data" && !(st.ue_reg&&st.ue_reg.up));
    const blk=duBlocked?'disabled title="PTP must be locked first"':dataBlocked?'disabled title="Register the UE first"':'';
    const startBtn=`<button class="btn go" data-c="${c.key}" data-a="start" ${blk}>Start</button>`;
    return `<div class="comp">
      <span class="dot ${cls}"></span>
      <span class="name">${c.label}</span>
      <span class="detail">${detail}</span>
      ${timerHtml(s)}
      <span class="acts">${startBtn}
        <button class="btn stop" data-c="${c.key}" data-a="stop">Stop</button></span>
    </div>`;
  }).join("");
  box.querySelectorAll("button:not([disabled])").forEach(b=>b.onclick=()=>action(b.dataset.c,b.dataset.a,b));
  tick();
}

async function action(component,act,btn){
  if(btn){btn.disabled=true;const t=btn.textContent;btn.textContent="...";
    btn._t=t;}
  let res={};
  try{res=await (await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({component,action:act})})).json();}catch(e){}
  if(res.rc && res.out && res.out.includes("BLOCKED")) alert(res.out);
  setTimeout(poll,1000);
  setTimeout(()=>{if(btn){btn.disabled=false;btn.textContent=btn._t||act;}},1500);
}

let polling=false;
async function poll(){
  if(polling) return; polling=true;
  try{
    const st=await (await fetch("/api/status")).json();
    if(st._now) srvOff=st._now*1000-Date.now();
    last=st;drawTopo(st);drawComponents(st);
  }catch(e){} finally{polling=false;}
}

let player=null;
function playFeed(){
  const v=document.getElementById("video"),fs=document.getElementById("feedStatus");
  if(player){try{player.destroy()}catch(e){};player=null;}
  if(!window.mpegts||!mpegts.isSupported()){fs.textContent="mpegts.js unsupported here";return;}
  fs.textContent="connecting to edge feed...";
  player=mpegts.createPlayer({type:"mpegts",isLive:true,url:location.origin+"/feed"},
    {enableWorker:true,liveBufferLatencyChasing:true,fixAudioTimestampGap:false});
  player.attachMediaElement(v);
  player.on(mpegts.Events.ERROR,(type,detail,info)=>{
    fs.textContent="feed error: "+type+" / "+detail+" / "+JSON.stringify(info||{});});
  player.on(mpegts.Events.MEDIA_INFO,()=>{fs.textContent="live";});
  player.load();v.play().catch(()=>{});
  clearInterval(window._fq);
  window._fq=setInterval(()=>{
    const q=v.getVideoPlaybackQuality?v.getVideoPlaybackQuality():{};
    if(v.videoWidth) fs.textContent=`live ${v.videoWidth}x${v.videoHeight} - ${q.totalVideoFrames||0} frames decoded`;
  },1000);
}

async function seq(action){
  const order=action==="start"?["core","cu","ptp"]:["stream","ue_data","ue_reg","du","ptp","cu","core"];
  for(const c of order){
    await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({component:c,action})});
    await new Promise(r=>setTimeout(r,action==="start"?2500:1200));poll();
  }
  if(action==="start") alert("Core, CU and PTP started. Follow the Next step bar: start each component once the one before it shows ready.");
}
document.getElementById("startAll").onclick=()=>seq("start");
document.getElementById("stopAll").onclick=()=>seq("stop");
document.getElementById("playFeed").onclick=playFeed;
setInterval(()=>document.getElementById("clock").textContent=new Date().toLocaleTimeString(),1000);
poll();setInterval(poll,2000);setInterval(tick,250);
// auto-start the feed on load (video is muted, so autoplay is allowed)
setTimeout(playFeed, 1200);
