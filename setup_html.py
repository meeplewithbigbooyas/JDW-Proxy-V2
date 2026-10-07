# -*- coding: utf-8 -*-
"""First-launch client setup wizard HTML for JDW Proxy V2 (bilingual EN/RU)."""

SETUP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>JDW Proxy V2 - Setup</title>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --panel2:#1c2330; --border:#2d3645;
    --text:#e6edf3; --muted:#8b949e; --accent:#4493f8; --accent2:#2ea043;
    --danger:#f85149; --warn:#d29922; --mono:'SFMono-Regular',Consolas,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
    font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:14px}
  header{display:flex;align-items:center;justify-content:space-between;
    padding:14px 22px;background:var(--panel);border-bottom:1px solid var(--border)}
  header h1{font-size:17px;margin:0;display:flex;align-items:center;gap:10px}
  .badge{background:#1f6feb33;color:var(--accent);border:1px solid var(--accent);
    border-radius:5px;padding:2px 8px;font-size:12px;font-weight:700;letter-spacing:.5px}
  .lang{display:flex;gap:6px}
  .lang button{background:var(--panel2);color:var(--muted);border:1px solid var(--border);
    border-radius:6px;padding:5px 11px;cursor:pointer;font-weight:600}
  .lang button.active{color:#fff;border-color:var(--accent);background:#1f6feb33}
  main{max-width:760px;margin:0 auto;padding:26px 22px 60px}
  .card{background:var(--panel);border:1px solid var(--border);border-radius:10px;
    padding:20px 22px;margin-bottom:18px}
  .card h2{font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin:0 0 12px}
  .intro{color:var(--text);line-height:1.6;margin:0 0 6px}
  .intro .muted{color:var(--muted)}
  .endpoint{background:var(--panel2);border:1px solid var(--border);border-radius:7px;
    padding:10px 12px;font-family:var(--mono);font-size:13px;color:var(--accent);word-break:break-all;margin:8px 0}
  ul.clients{list-style:none;margin:0;padding:0}
  ul.clients li{display:flex;align-items:center;gap:12px;padding:11px 12px;border:1px solid var(--border);
    border-radius:8px;margin-bottom:9px;background:var(--panel2);cursor:pointer;transition:.15s}
  ul.clients li:hover{border-color:var(--accent)}
  ul.clients li.sel{border-color:var(--accent2);background:#2ea04318}
  .num{flex:none;width:30px;height:30px;border-radius:7px;background:#30363d;color:#fff;
    display:flex;align-items:center;justify-content:center;font-weight:700;font-family:var(--mono)}
  li.sel .num{background:var(--accent2)}
  .cinfo{flex:1;min-width:0}
  .cinfo .cname{font-weight:600}
  .cinfo .cpath{color:var(--muted);font-size:12px;font-family:var(--mono);word-break:break-all}
  .pill{flex:none;font-size:11px;font-weight:600;padding:2px 8px;border-radius:5px}
  .pill.found{background:#2ea04322;color:#3fb950}
  .pill.miss{background:#8b949e22;color:var(--muted)}
  .selfield{margin:14px 0 4px}
  .selfield span{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}
  input[type=text]{background:var(--panel2);color:var(--text);border:1px solid var(--border);
    border-radius:6px;padding:9px 11px;font-family:var(--mono);font-size:15px;width:100%;letter-spacing:2px}
  .warn{background:#d2992216;border:1px solid #d2992255;border-radius:8px;padding:12px 14px;
    color:#e3b341;line-height:1.55;margin:4px 0 0;font-size:13px}
  .warn b{color:#f0c761}
  label.chk{display:flex;align-items:flex-start;gap:9px;color:var(--text);line-height:1.5;
    margin-top:14px;cursor:pointer;font-size:13px}
  label.chk input{margin-top:3px}
  label.chk .muted{color:var(--muted)}
  .actions{display:flex;gap:10px;align-items:center;margin-top:18px;flex-wrap:wrap}
  button.primary{background:var(--accent2);color:#fff;border:none;border-radius:7px;
    padding:11px 20px;cursor:pointer;font-weight:600;font-size:14px}
  button.primary:hover{filter:brightness(1.1)}
  button.primary:disabled{opacity:.5;cursor:not-allowed}
  button.ghost{background:transparent;color:var(--muted);border:1px solid var(--border);
    border-radius:7px;padding:11px 18px;cursor:pointer;font-size:14px}
  button.ghost:hover{color:var(--text);border-color:var(--muted)}
  .results{margin-top:4px}
  .ritem{display:flex;align-items:flex-start;gap:10px;padding:11px 12px;border:1px solid var(--border);
    border-radius:8px;margin-bottom:9px;background:var(--panel2)}
  .rico{flex:none;width:22px;height:22px;border-radius:50%;display:flex;align-items:center;
    justify-content:center;font-weight:700;font-size:13px;margin-top:1px}
  .rico.ok{background:#2ea04322;color:#3fb950}
  .rico.bad{background:#f8514922;color:#f85149}
  .rico.adm{background:#d2992222;color:#e3b341}
  .rbody{flex:1;min-width:0}
  .rbody .rname{font-weight:600}
  .rbody .rnote{color:var(--muted);font-size:12px;font-family:var(--mono);word-break:break-all;margin-top:2px}
  .hidden{display:none}
  a.dash{color:var(--accent);text-decoration:none}
  a.dash:hover{text-decoration:underline}
  .spin{display:inline-block;width:14px;height:14px;border:2px solid #ffffff55;
    border-top-color:#fff;border-radius:50%;animation:sp .7s linear infinite;vertical-align:-2px;margin-right:7px}
  @keyframes sp{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<header>
  <h1><span class="badge">JDW V2</span> <span data-i="title">Client setup</span></h1>
  <div class="lang">
    <button id="langEn" class="active" onclick="setLang('en')">EN</button>
    <button id="langRu" onclick="setLang('ru')">RU</button>
  </div>
</header>

<main>
  <div class="card">
    <p class="intro" data-i="welcome">Welcome to JDW Proxy. Let's connect your AI clients to the proxy.</p>
    <p class="intro muted" data-i="welcome2">The proxy will automatically find each selected client's configuration file and add a <b>JDW</b> provider pointing at this local proxy.</p>
    <div class="selfield"><span data-i="endpointLbl">Clients will be pointed at this endpoint:</span></div>
    <div class="endpoint" id="endpointUrl">http://127.0.0.1:8181/v1</div>
  </div>

  <div class="card">
    <h2 data-i="chooseTitle">1. Choose your AI client(s)</h2>
    <p class="intro muted" data-i="chooseHint" style="margin-bottom:14px">Click to select, or type the numbers below (e.g. <b>134</b> = clients 1, 3 and 4).</p>
    <ul class="clients" id="clientList"></ul>
    <div class="selfield">
      <span data-i="selLbl">Selected clients (numbers, no spaces):</span>
      <input type="text" id="selection" placeholder="134" oninput="onSelInput()" autocomplete="off"/>
    </div>
  </div>

  <div class="card">
    <h2 data-i="keyTitle">2. About your API key</h2>
    <div class="warn">
      <span data-i="keyWarn">You must enter your own JDW API key.</span>
      <span data-i="keyWarn2" class="muted"></span>
    </div>
    <label class="chk">
      <input type="checkbox" id="writeKey"/>
      <span><span data-i="writeKeyLbl">Also write my saved proxy key into the client configs now</span>
      <span class="muted" data-i="writeKeyHint"> (optional - otherwise set it yourself later)</span></span>
    </label>
  </div>

  <div class="card" id="resultsCard" style="display:none">
    <h2 data-i="resultsTitle">Results</h2>
    <div class="results" id="results"></div>
    <div id="adminRow" class="hidden" style="margin-top:6px">
      <p class="warn" data-i="adminMsg">Some files could not be written without administrator rights.</p>
      <button class="primary" onclick="retryElevated()" data-i="adminBtn" style="margin-top:10px">Retry as administrator</button>
    </div>
  </div>

  <div class="actions">
    <button class="primary" id="applyBtn" onclick="applySetup()" data-i="apply">Configure selected clients</button>
    <button class="ghost" onclick="skipSetup()" data-i="skip">Skip for now</button>
    <a class="dash" href="/" data-i="toDash">Go to dashboard &rarr;</a>
  </div>
</main>

<script>
const I18N = {
  en:{
    title:"Client setup",
    welcome:"Welcome to JDW Proxy. Let's connect your AI clients to the proxy.",
    welcome2:"The proxy will automatically find each selected client's configuration file and add a <b>JDW</b> provider pointing at this local proxy.",
    endpointLbl:"Clients will be pointed at this endpoint:",
    chooseTitle:"1. Choose your AI client(s)",
    chooseHint:"Click to select, or type the numbers below (e.g. <b>134</b> = clients 1, 3 and 4).",
    selLbl:"Selected clients (numbers, no spaces):",
    keyTitle:"2. About your API key",
    keyWarn:"You must enter your own JDW API key.",
    keyWarn2:"The setup wires each client to the proxy, but it will NOT invent a key for you. Put your JDW key into the client (or set the ",
    writeKeyLbl:"Also write my saved proxy key into the client configs now",
    writeKeyHint:" (optional - otherwise set it yourself later)",
    resultsTitle:"Results",
    adminMsg:"Some files could not be written without administrator rights.",
    adminBtn:"Retry as administrator",
    apply:"Configure selected clients",
    skip:"Skip for now",
    toDash:"Go to dashboard \u2192",
    found:"installed", miss:"not found",
    needSel:"Select at least one client first.",
    working:"Configuring...",
    doneOk:"Done! Your clients now point at the JDW proxy. Remember to set your API key.",
    skipped:"Setup skipped. Opening the dashboard...",
    envName:"environment variable"
  },
  ru:{
    title:"\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 \u043a\u043b\u0438\u0435\u043d\u0442\u043e\u0432",
    welcome:"\u0414\u043e\u0431\u0440\u043e \u043f\u043e\u0436\u0430\u043b\u043e\u0432\u0430\u0442\u044c \u0432 JDW Proxy. \u041f\u043e\u0434\u043a\u043b\u044e\u0447\u0438\u043c \u0432\u0430\u0448\u0438 AI-\u043a\u043b\u0438\u0435\u043d\u0442\u044b \u043a \u043f\u0440\u043e\u043a\u0441\u0438.",
    welcome2:"\u041f\u0440\u043e\u043a\u0441\u0438 \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438 \u043d\u0430\u0439\u0434\u0451\u0442 \u0444\u0430\u0439\u043b \u043a\u043e\u043d\u0444\u0438\u0433\u0443\u0440\u0430\u0446\u0438\u0438 \u043a\u0430\u0436\u0434\u043e\u0433\u043e \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u043e\u0433\u043e \u043a\u043b\u0438\u0435\u043d\u0442\u0430 \u0438 \u0434\u043e\u0431\u0430\u0432\u0438\u0442 \u043f\u0440\u043e\u0432\u0430\u0439\u0434\u0435\u0440 <b>JDW</b>, \u0443\u043a\u0430\u0437\u044b\u0432\u0430\u044e\u0449\u0438\u0439 \u043d\u0430 \u044d\u0442\u043e \u043b\u043e\u043a\u0430\u043b\u044c\u043d\u043e\u0435 \u043f\u0440\u043e\u043a\u0441\u0438.",
    endpointLbl:"\u041a\u043b\u0438\u0435\u043d\u0442\u044b \u0431\u0443\u0434\u0443\u0442 \u043d\u0430\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u044b \u043d\u0430 \u044d\u0442\u043e\u0442 \u0430\u0434\u0440\u0435\u0441:",
    chooseTitle:"1. \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0432\u0430\u0448 AI-\u043a\u043b\u0438\u0435\u043d\u0442(\u044b)",
    chooseHint:"\u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u0434\u043b\u044f \u0432\u044b\u0431\u043e\u0440\u0430 \u0438\u043b\u0438 \u0432\u0432\u0435\u0434\u0438\u0442\u0435 \u043d\u043e\u043c\u0435\u0440\u0430 \u043d\u0438\u0436\u0435 (\u043d\u0430\u043f\u0440. <b>134</b> = \u043a\u043b\u0438\u0435\u043d\u0442\u044b 1, 3 \u0438 4).",
    selLbl:"\u0412\u044b\u0431\u0440\u0430\u043d\u043d\u044b\u0435 \u043a\u043b\u0438\u0435\u043d\u0442\u044b (\u043d\u043e\u043c\u0435\u0440\u0430, \u0431\u0435\u0437 \u043f\u0440\u043e\u0431\u0435\u043b\u043e\u0432):",
    keyTitle:"2. \u041e \u0432\u0430\u0448\u0435\u043c API-\u043a\u043b\u044e\u0447\u0435",
    keyWarn:"\u0412\u044b \u0434\u043e\u043b\u0436\u043d\u044b \u0441\u0430\u043c\u0438 \u0432\u0432\u0435\u0441\u0442\u0438 \u0441\u0432\u043e\u0439 API-\u043a\u043b\u044e\u0447 JDW.",
    keyWarn2:"\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0430\u0435\u0442 \u043a\u0430\u0436\u0434\u043e\u0433\u043e \u043a\u043b\u0438\u0435\u043d\u0442\u0430 \u043a \u043f\u0440\u043e\u043a\u0441\u0438, \u043d\u043e \u041d\u0415 \u043f\u0440\u0438\u0434\u0443\u043c\u044b\u0432\u0430\u0435\u0442 \u043a\u043b\u044e\u0447 \u0437\u0430 \u0432\u0430\u0441. \u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0441\u0432\u043e\u0439 \u043a\u043b\u044e\u0447 JDW \u0432 \u043a\u043b\u0438\u0435\u043d\u0442\u0435 (\u0438\u043b\u0438 \u0432 \u043f\u0435\u0440\u0435\u043c\u0435\u043d\u043d\u043e\u0439 ",
    writeKeyLbl:"\u0422\u0430\u043a\u0436\u0435 \u0437\u0430\u043f\u0438\u0441\u0430\u0442\u044c \u043c\u043e\u0439 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0439 \u043a\u043b\u044e\u0447 \u0432 \u043a\u043e\u043d\u0444\u0438\u0433\u0438 \u043a\u043b\u0438\u0435\u043d\u0442\u043e\u0432 \u0441\u0435\u0439\u0447\u0430\u0441",
    writeKeyHint:" (\u043d\u0435\u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u043e - \u0438\u043d\u0430\u0447\u0435 \u0443\u043a\u0430\u0436\u0438\u0442\u0435 \u0435\u0433\u043e \u043f\u043e\u0437\u0436\u0435 \u0441\u0430\u043c\u0438)",
    resultsTitle:"\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b",
    adminMsg:"\u041d\u0435\u043a\u043e\u0442\u043e\u0440\u044b\u0435 \u0444\u0430\u0439\u043b\u044b \u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0437\u0430\u043f\u0438\u0441\u0430\u0442\u044c \u0431\u0435\u0437 \u043f\u0440\u0430\u0432 \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430.",
    adminBtn:"\u041f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u044c \u043e\u0442 \u0438\u043c\u0435\u043d\u0438 \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430",
    apply:"\u041d\u0430\u0441\u0442\u0440\u043e\u0438\u0442\u044c \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u044b\u0445 \u043a\u043b\u0438\u0435\u043d\u0442\u043e\u0432",
    skip:"\u041f\u0440\u043e\u043f\u0443\u0441\u0442\u0438\u0442\u044c",
    toDash:"\u041a \u043f\u0430\u043d\u0435\u043b\u0438 \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f \u2192",
    found:"\u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d", miss:"\u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d",
    needSel:"\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0445\u043e\u0442\u044f \u0431\u044b \u043e\u0434\u043d\u043e\u0433\u043e \u043a\u043b\u0438\u0435\u043d\u0442\u0430.",
    working:"\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430...",
    doneOk:"\u0413\u043e\u0442\u043e\u0432\u043e! \u041a\u043b\u0438\u0435\u043d\u0442\u044b \u0442\u0435\u043f\u0435\u0440\u044c \u0443\u043a\u0430\u0437\u044b\u0432\u0430\u044e\u0442 \u043d\u0430 JDW proxy. \u041d\u0435 \u0437\u0430\u0431\u0443\u0434\u044c\u0442\u0435 \u0443\u043a\u0430\u0437\u0430\u0442\u044c \u0441\u0432\u043e\u0439 API-\u043a\u043b\u044e\u0447.",
    skipped:"\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 \u043f\u0440\u043e\u043f\u0443\u0449\u0435\u043d\u0430. \u041e\u0442\u043a\u0440\u044b\u0432\u0430\u0435\u043c \u043f\u0430\u043d\u0435\u043b\u044c...",
    envName:"\u043f\u0435\u0440\u0435\u043c\u0435\u043d\u043d\u043e\u0439 \u043e\u043a\u0440\u0443\u0436\u0435\u043d\u0438\u044f"
  }
};

let LANG = "en";
let CLIENTS = [];
let META = {base_url:"", model:"", api_key_env:"JDW_API_KEY"};

function t(k){ return (I18N[LANG] && I18N[LANG][k]) || I18N.en[k] || k; }

function setLang(l){
  LANG = l;
  document.getElementById("langEn").classList.toggle("active", l==="en");
  document.getElementById("langRu").classList.toggle("active", l==="ru");
  document.documentElement.lang = l;
  document.querySelectorAll("[data-i]").forEach(function(el){
    el.innerHTML = t(el.getAttribute("data-i"));
  });
  // dynamic key-warning tail with the env var name
  var w2 = document.querySelector('[data-i="keyWarn2"]');
  if(w2){ w2.innerHTML = t("keyWarn2") + "<b>" + META.api_key_env + "</b> " + t("envName") + ")."; }
  renderClients();
}

function renderClients(){
  var ul = document.getElementById("clientList");
  ul.innerHTML = "";
  var sel = parseSel(document.getElementById("selection").value);
  CLIENTS.forEach(function(c){
    var li = document.createElement("li");
    li.className = sel.indexOf(c.number)>=0 ? "sel" : "";
    li.onclick = function(){ toggle(c.number); };
    var pill = c.installed
      ? '<span class="pill found">'+t("found")+'</span>'
      : '<span class="pill miss">'+t("miss")+'</span>';
    li.innerHTML =
      '<span class="num">'+c.number+'</span>'+
      '<span class="cinfo"><span class="cname">'+c.name+'</span>'+
      '<span class="cpath">'+c.config_hint+'</span></span>'+pill;
    ul.appendChild(li);
  });
}

function parseSel(str){
  var out = [];
  str = (str||"").replace(/[^0-9]/g,"");
  for(var i=0;i<str.length;i++){
    var n = parseInt(str[i],10);
    if(n>=1 && n<=CLIENTS.length && out.indexOf(n)<0) out.push(n);
  }
  return out;
}

function toggle(n){
  var sel = parseSel(document.getElementById("selection").value);
  var i = sel.indexOf(n);
  if(i>=0) sel.splice(i,1); else sel.push(n);
  sel.sort();
  document.getElementById("selection").value = sel.join("");
  renderClients();
}

function onSelInput(){
  var el = document.getElementById("selection");
  el.value = el.value.replace(/[^0-9]/g,"");
  renderClients();
}

async function loadClients(){
  try{
    var r = await fetch("/setup/clients");
    var d = await r.json();
    CLIENTS = d.clients || [];
    META.base_url = d.base_url; META.model = d.model;
    META.api_key_env = d.api_key_env || "JDW_API_KEY";
    document.getElementById("endpointUrl").textContent = d.base_url;
    // pre-select installed clients
    var pre = CLIENTS.filter(function(c){return c.installed;}).map(function(c){return c.number;});
    document.getElementById("selection").value = pre.join("");
    setLang(LANG);
  }catch(e){ console.error(e); }
}

function renderResults(report){
  var card = document.getElementById("resultsCard");
  var box = document.getElementById("results");
  card.style.display = "block";
  box.innerHTML = "";
  (report.results||[]).forEach(function(r){
    var cls = r.ok ? "ok" : (r.needs_admin ? "adm" : "bad");
    var ico = r.ok ? "\u2713" : (r.needs_admin ? "\u26a0" : "\u2717");
    var div = document.createElement("div");
    div.className = "ritem";
    div.innerHTML =
      '<span class="rico '+cls+'">'+ico+'</span>'+
      '<span class="rbody"><span class="rname">'+r.name+'</span>'+
      '<span class="rnote">'+(r.path||"")+(r.note? "  \u2014  "+r.note : "")+'</span></span>';
    box.appendChild(div);
  });
  document.getElementById("adminRow").classList.toggle("hidden", !report.needs_admin);
  card.scrollIntoView({behavior:"smooth", block:"nearest"});
}

async function applySetup(){
  var sel = document.getElementById("selection").value;
  if(parseSel(sel).length===0){ alert(t("needSel")); return; }
  var btn = document.getElementById("applyBtn");
  btn.disabled = true;
  var old = btn.innerHTML;
  btn.innerHTML = '<span class="spin"></span>'+t("working");
  try{
    var r = await fetch("/setup/apply", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({selection: sel,
        write_key: document.getElementById("writeKey").checked})});
    var report = await r.json();
    renderResults(report);
  }catch(e){ alert("Error: "+e); }
  btn.disabled = false; btn.innerHTML = old;
}

async function retryElevated(){
  var sel = document.getElementById("selection").value;
  var r = await fetch("/setup/elevate", {method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({selection: sel,
      write_key: document.getElementById("writeKey").checked})});
  var report = await r.json();
  if(report.error){ alert(report.error + (report.detail? "\n"+report.detail : "")); return; }
  renderResults(report);
}

async function skipSetup(){
  try{ await fetch("/setup/skip", {method:"POST"}); }catch(e){}
  window.location.href = "/";
}

loadClients();
</script>
</body>
</html>
"""
