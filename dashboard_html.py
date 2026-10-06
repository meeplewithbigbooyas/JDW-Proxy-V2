# -*- coding: utf-8 -*-
"""Dashboard HTML for JDW Fix Proxy (bilingual EN/RU, single-file)."""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>JDW Fix Proxy</title>
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
  .dot{width:10px;height:10px;border-radius:50%;background:var(--muted)}
  .dot.on{background:var(--accent2);box-shadow:0 0 8px var(--accent2)}
  .dot.off{background:var(--danger)}
  .lang{display:flex;gap:6px}
  .lang button{background:var(--panel2);color:var(--muted);border:1px solid var(--border);
    border-radius:6px;padding:5px 11px;cursor:pointer;font-weight:600}
  .lang button.active{color:#fff;border-color:var(--accent);background:#1f6feb33}
  main{display:grid;grid-template-columns:380px 1fr;gap:18px;padding:18px 22px;max-width:1400px}
  .card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px 18px;margin-bottom:16px}
  .card h2{font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin:0 0 14px}
  .row{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #ffffff0d}
  .row:last-child{border-bottom:none}
  .row label{color:var(--text);flex:1}
  .row .hint{color:var(--muted);font-size:12px;display:block;margin-top:2px}
  .mono{font-family:var(--mono)}
  input[type=text],select{background:var(--panel2);color:var(--text);border:1px solid var(--border);
    border-radius:6px;padding:7px 9px;font-family:var(--mono);font-size:13px;width:100%}
  textarea{background:var(--panel2);color:var(--text);border:1px solid var(--border);
    border-radius:6px;padding:7px 9px;font-family:var(--mono);font-size:13px;width:100%}
  .fbrow{display:flex;gap:6px;align-items:center;margin-bottom:6px}
  .fbrow input{flex:1}
  .delkey{flex:none;background:var(--panel2);color:var(--muted);border:1px solid var(--border);
    border-radius:6px;width:30px;height:32px;cursor:pointer;font-size:16px;line-height:1;
    display:flex;align-items:center;justify-content:center;transition:.15s}
  .delkey:hover{color:var(--danger);border-color:var(--danger)}
  .addkey{display:inline-flex;align-items:center;gap:6px;margin-top:4px;
    background:transparent;color:var(--accent);border:1px dashed var(--border);
    border-radius:6px;padding:6px 11px;cursor:pointer;font-size:12.5px;font-weight:600;transition:.15s}
  .addkey:hover{border-color:var(--accent);background:#1f6feb14}
  .addkey .plus{font-size:15px;line-height:1;font-weight:700}
  .field{margin-bottom:12px}
  .field > span{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}
  .switch{position:relative;width:42px;height:24px;flex:none}
  .switch input{opacity:0;width:0;height:0}
  .slider{position:absolute;inset:0;background:#30363d;border-radius:24px;cursor:pointer;transition:.2s}
  .slider:before{content:"";position:absolute;height:18px;width:18px;left:3px;top:3px;background:#8b949e;border-radius:50%;transition:.2s}
  input:checked+.slider{background:#1f6feb55}
  input:checked+.slider:before{transform:translateX(18px);background:var(--accent)}
  button.primary{background:var(--accent2);color:#fff;border:none;border-radius:7px;padding:9px 16px;cursor:pointer;font-weight:600;width:100%}
  button.primary:hover{filter:brightness(1.1)}
  button.ghost{background:transparent;color:var(--muted);border:1px solid var(--border);border-radius:6px;padding:6px 12px;cursor:pointer}
  .endpoint{background:var(--panel2);border:1px solid var(--border);border-radius:7px;padding:10px 12px;font-family:var(--mono);font-size:13px;color:var(--accent);word-break:break-all}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--border);white-space:nowrap}
  th{color:var(--muted);font-weight:600;text-transform:uppercase;font-size:11px;letter-spacing:.4px}
  td.mono{font-family:var(--mono)}
  .tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;font-weight:600}
  .tag.ok{background:#2ea04322;color:#3fb950}
  .tag.tool{background:#8957e522;color:#a371f7}
  .tag.err{background:#f8514922;color:#f85149}
  .tag.stream{background:#1f6feb22;color:#4493f8}
  .toolbar{display:flex;gap:10px;align-items:center;margin-bottom:12px}
  .toolbar h2{margin:0;flex:1}
  .saved{color:var(--accent2);font-size:12px;opacity:0;transition:opacity .3s}
  .saved.show{opacity:1}
  .empty{color:var(--muted);text-align:center;padding:30px}
  @media(max-width:900px){main{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1><span id="statusDot" class="dot"></span> JDW Fix Proxy</h1>
  <div class="lang">
    <button id="langEn" class="active" onclick="setLang('en')">EN</button>
    <button id="langRu" onclick="setLang('ru')">RU</button>
  </div>
</header>

<main>
  <div>
    <div class="card">
      <h2 data-i="endpointTitle">Client endpoint</h2>
      <div class="endpoint" id="endpointUrl">http://localhost:8181/v1</div>
      <p class="hint" data-i="endpointHint" style="color:var(--muted);margin:10px 0 0;font-size:12px">
        Point your Anthropic client's base URL here. Use your JDW key as the API key.</p>
    </div>

    <div class="card">
      <h2 data-i="connTitle">Connection</h2>
      <div class="field">
        <span data-i="upstream">Upstream base URL</span>
        <input type="text" id="upstream"/>
      </div>
      <div class="field">
        <span data-i="model">Model</span>
        <input type="text" id="model"/>
      </div>
      <div class="field">
        <span data-i="apikey">API key (leave masked to keep current)</span>
        <input type="text" id="apikey" placeholder=""/>
      </div>
      <div class="field">
        <span data-i="fbkeys">Fallback API keys (auto-tried if the main key is rejected)</span>
        <div id="fbkeysWrap"></div>
        <button type="button" class="addkey" id="addFbKey" onclick="addFbKeyRow('')">
          <span class="plus">+</span><span data-i="addkey">Add fallback key</span>
        </button>
      </div>
    </div>

    <div class="card">
      <h2 data-i="fixTitle">Protocol fixes</h2>
      <div class="row">
        <label data-i="f_tool">Tool injection<span class="hint" data-i="f_tool_h">Inject client tools into the system prompt and rewrite JSON calls into real tool_use blocks.</span></label>
        <span class="switch"><input type="checkbox" id="tool_injection"><span class="slider"></span></span>
      </div>
      <div class="row">
        <label data-i="f_strict">Strict tool names<span class="hint" data-i="f_strict_h">Only allow calls to tools the client actually offered; unknown names are kept as text so the client never sees "unknown tool".</span></label>
        <span class="switch"><input type="checkbox" id="strict_tool_names"><span class="slider"></span></span>
      </div>
      <div class="row">
        <label data-i="f_flat">Flatten tool history<span class="hint" data-i="f_flat_h">Render past tool_use / tool_result into text the model can read.</span></label>
        <span class="switch"><input type="checkbox" id="flatten_tool_history"><span class="slider"></span></span>
      </div>
      <div class="row">
        <label data-i="f_think">Strip thinking<span class="hint" data-i="f_think_h">Remove leaked thinking blocks from the response content.</span></label>
        <span class="switch"><input type="checkbox" id="strip_thinking"><span class="slider"></span></span>
      </div>
      <div class="row">
        <label data-i="f_max">Enforce max_tokens<span class="hint" data-i="f_max_h">Truncate output and report stop_reason=max_tokens.</span></label>
        <span class="switch"><input type="checkbox" id="enforce_max_tokens"><span class="slider"></span></span>
      </div>
      <div class="row">
        <label data-i="f_stop">Enforce stop_sequences<span class="hint" data-i="f_stop_h">Cut text before the stop word and report it.</span></label>
        <span class="switch"><input type="checkbox" id="enforce_stop_sequences"><span class="slider"></span></span>
      </div>
      <div class="row">
        <label data-i="f_compact">Compact tool schemas<span class="hint" data-i="f_compact_h">Send a short description + field names instead of full JSON schemas. Big token savings per request.</span></label>
        <span class="switch"><input type="checkbox" id="compact_tool_schemas"><span class="slider"></span></span>
      </div>
      <div class="row">
        <label data-i="f_ultra">Ultra-compact tools<span class="hint" data-i="f_ultra_h">One line per tool (name + field names only). Maximum tool-token saving; use if models still handle calls well.</span></label>
        <span class="switch"><input type="checkbox" id="ultra_compact_tools"><span class="slider"></span></span>
      </div>
      <div class="field" style="margin-top:14px">
        <span data-i="maxhist">Max history messages (0 = unlimited)</span>
        <input type="text" id="max_history_messages"/>
      </div>
      <div class="field">
        <span data-i="maxtool">Max tool-result chars (0 = no limit)</span>
        <input type="text" id="max_tool_result_chars"/>
      </div>
      <div class="field">
        <span data-i="maxout">Max output tokens (0 = model default)</span>
        <input type="text" id="max_output_tokens"/>
      </div>
      <div class="field" style="margin-top:14px">
        <label><input type="checkbox" id="web_tools_enabled"/> <span data-i="webtools">Enable image tool (fetch_image) — run by the proxy</span></label>
      </div>
      <div class="field">
        <span data-i="webiters">Image tool max round-trips</span>
        <input type="text" id="web_tools_max_iters"/>
      </div>
      <div class="field" style="margin-top:14px">
        <span data-i="usage_mode">Usage reporting mode</span>
        <select id="usage_mode">
          <option value="normalized">normalized (subtract baseline)</option>
          <option value="raw">raw (upstream numbers)</option>
          <option value="estimated">estimated (local heuristic)</option>
        </select>
      </div>
      <div class="field">
        <span data-i="baseline">Usage baseline tokens</span>
        <input type="text" id="usage_baseline_tokens"/>
      </div>
      <button class="primary" onclick="saveConfig()" data-i="save">Save settings</button>
      <button class="danger" onclick="stopProxy()" data-i="stop" style="margin-left:8px;background:#c0392b;color:#fff;border:none;padding:8px 14px;border-radius:6px;cursor:pointer">Stop proxy</button>
      <span class="saved" id="savedMsg" data-i="saved">Saved</span>
    </div>
  </div>

  <div>
    <div class="card">
      <div class="toolbar">
        <h2 data-i="logTitle">Live request log</h2>
        <button class="ghost" onclick="clearLogs()" data-i="clear">Clear</button>
      </div>
      <table>
        <thead><tr>
          <th data-i="c_time">Time</th>
          <th data-i="c_model">Model</th>
          <th data-i="c_mode">Mode</th>
          <th data-i="c_tools">Tools</th>
          <th data-i="c_stop">Stop</th>
          <th data-i="c_usage">Usage (in/out)</th>
          <th data-i="c_lat">Lat</th>
        </tr></thead>
        <tbody id="logBody">
          <tr><td colspan="7" class="empty" data-i="noLogs">No requests yet.</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</main>

<script>
const I18N = {
  en:{
    endpointTitle:"Client endpoint", endpointHint:"Point your Anthropic client's base URL here. Use your JDW key as the API key.",
    connTitle:"Connection", upstream:"Upstream base URL", model:"Model", apikey:"API key (leave masked to keep current)",
    fbkeys:"Fallback API keys (auto-tried if the main key is rejected)", addkey:"Add fallback key",
    fixTitle:"Protocol fixes",
    f_tool:"Tool injection", f_tool_h:"Inject client tools into the system prompt and rewrite JSON calls into real tool_use blocks.",
    f_strict:"Strict tool names", f_strict_h:"Only allow calls to tools the client actually offered; unknown names are kept as text so the client never sees \"unknown tool\".",
    f_flat:"Flatten tool history", f_flat_h:"Render past tool_use / tool_result into text the model can read.",
    f_think:"Strip thinking", f_think_h:"Remove leaked thinking blocks from the response content.",
    f_max:"Enforce max_tokens", f_max_h:"Truncate output and report stop_reason=max_tokens.",
    f_stop:"Enforce stop_sequences", f_stop_h:"Cut text before the stop word and report it.",
    f_compact:"Compact tool schemas", f_compact_h:"Send a short description + field names instead of full JSON schemas. Big token savings per request.",
    f_ultra:"Ultra-compact tools", f_ultra_h:"One line per tool (name + field names only). Maximum tool-token saving; use if models still handle calls well.",
    maxhist:"Max history messages (0 = unlimited)", maxtool:"Max tool-result chars (0 = no limit)",
    maxout:"Max output tokens (0 = model default)",
    webtools:"Enable image tool (fetch_image) — run by the proxy",
    webiters:"Image tool max round-trips",
    usage_mode:"Usage reporting mode", baseline:"Usage baseline tokens",
    save:"Save settings", stop:"Stop proxy", saved:"Saved",
    logTitle:"Live request log", clear:"Clear",
    c_time:"Time", c_model:"Model", c_mode:"Mode", c_tools:"Tools", c_stop:"Stop", c_usage:"Usage (in/out)", c_lat:"Lat",
    noLogs:"No requests yet."
  },
  ru:{
    endpointTitle:"Адрес для клиента", endpointHint:"Укажите этот base URL в вашем Anthropic-клиенте. Ключ API — ваш ключ JDW.",
    connTitle:"Подключение", upstream:"Upstream base URL", model:"Модель", apikey:"API-ключ (оставьте маску, чтобы не менять)",
    fbkeys:"Резервные API-ключи (пробуются автоматически, если основной отклонён)", addkey:"Добавить резервный ключ",
    fixTitle:"Исправления протокола",
    f_tool:"Инъекция тулов", f_tool_h:"Добавляет тулы клиента в system-промпт и переписывает JSON-вызовы в настоящие tool_use блоки.",
    f_strict:"Строгие имена тулов", f_strict_h:"Разрешать вызов только тех тулов, которые прислал клиент; неизвестные имена остаются текстом, чтобы клиент не получил \"unknown tool\".",
    f_flat:"Разворачивать историю тулов", f_flat_h:"Превращает прошлые tool_use / tool_result в текст, который модель может прочитать.",
    f_think:"Убирать thinking", f_think_h:"Удаляет протёкшие thinking-блоки из контента ответа.",
    f_max:"Соблюдать max_tokens", f_max_h:"Обрезает ответ и ставит stop_reason=max_tokens.",
    f_stop:"Соблюдать stop_sequences", f_stop_h:"Обрезает текст до стоп-слова и сообщает о нём.",
    f_compact:"Компактные схемы тулов", f_compact_h:"Отправляет краткое описание + имена полей вместо полных JSON-схем. Большая экономия токенов на запрос.",
    f_ultra:"Ультра-компактные тулы", f_ultra_h:"Одна строка на тул (имя + имена полей). Максимальная экономия токенов тулов.",
    maxhist:"Макс. сообщений истории (0 = без лимита)", maxtool:"Макс. символов в результате тула (0 = без лимита)",
    maxout:"Макс. токенов вывода (0 = по умолчанию)",
    webtools:"Включить веб-инструменты (поиск / загрузка страницы / загрузка картинки) — выполняет прокси",
    webiters:"Макс. раундов веб-инструментов", webresults:"Кол-во результатов поиска", webchars:"Макс. символов при загрузке",
    usage_mode:"Режим подсчёта usage", baseline:"Базовая величина usage (токены)",
    save:"Сохранить настройки", stop:"Остановить прокси", saved:"Сохранено",
    logTitle:"Живой лог запросов", clear:"Очистить",
    c_time:"Время", c_model:"Модель", c_mode:"Режим", c_tools:"Тулы", c_stop:"Stop", c_usage:"Usage (вх/исх)", c_lat:"Задержка",
    noLogs:"Запросов пока нет."
  }
};
let LANG = "en";

function setLang(l, persist){
  if(persist===undefined) persist=true;
  LANG=l;
  document.getElementById('langEn').classList.toggle('active', l==='en');
  document.getElementById('langRu').classList.toggle('active', l==='ru');
  document.querySelectorAll('[data-i]').forEach(el=>{
    const k=el.getAttribute('data-i');
    if(I18N[l][k]!==undefined){
      const hint=el.querySelector('.hint');
      if(hint){ el.childNodes[0].nodeValue=I18N[l][k]; }
      else el.textContent=I18N[l][k];
    }
  });
  // hints inside labels
  document.querySelectorAll('.hint[data-i]').forEach(el=>{
    const k=el.getAttribute('data-i'); if(I18N[l][k]!==undefined) el.textContent=I18N[l][k];
  });
  if(persist) saveLangPref(l);
  renderLogs(LAST_LOGS);
}
function saveLangPref(l){ fetch('/admin/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ui_lang:l})}); }

function fbRowHtml(val){
  const safe=(val||'').replace(/"/g,'&quot;');
  return '<div class="fbrow">'+
    '<input type="text" class="mono" placeholder="sk-..." value="'+safe+'"/>'+
    '<button type="button" class="delkey" title="remove" onclick="this.parentNode.remove()">\u00d7</button>'+
  '</div>';
}
function renderFbKeys(list){
  const wrap=document.getElementById('fbkeysWrap');
  wrap.innerHTML=(list&&list.length?list:[]).map(fbRowHtml).join('');
}
function addFbKeyRow(val){
  const wrap=document.getElementById('fbkeysWrap');
  wrap.insertAdjacentHTML('beforeend', fbRowHtml(val||''));
  const rows=wrap.querySelectorAll('.fbrow input');
  if(rows.length) rows[rows.length-1].focus();
}

async function loadConfig(){
  const r=await fetch('/admin/config'); const c=await r.json();
  document.getElementById('upstream').value=c.upstream_base_url||'';
  document.getElementById('model').value=c.model||'';
  document.getElementById('apikey').value=c.api_key_masked||'';
  renderFbKeys(c.fallback_api_keys_masked||[]);
  const f=c.features||{};
  document.getElementById('tool_injection').checked=!!f.tool_injection;
  document.getElementById('strict_tool_names').checked=f.strict_tool_names!==false;
  document.getElementById('flatten_tool_history').checked=!!f.flatten_tool_history;
  document.getElementById('strip_thinking').checked=!!f.strip_thinking;
  document.getElementById('enforce_max_tokens').checked=!!f.enforce_max_tokens;
  document.getElementById('enforce_stop_sequences').checked=!!f.enforce_stop_sequences;
  document.getElementById('compact_tool_schemas').checked=f.compact_tool_schemas!==false;
  document.getElementById('ultra_compact_tools').checked=!!f.ultra_compact_tools;
  document.getElementById('max_history_messages').value=f.max_history_messages||0;
  document.getElementById('max_tool_result_chars').value=f.max_tool_result_chars||0;
  document.getElementById('max_output_tokens').value=f.max_output_tokens||0;
  document.getElementById('web_tools_enabled').checked=!!f.web_tools_enabled;
  document.getElementById('web_tools_max_iters').value=f.web_tools_max_iters||4;
  document.getElementById('usage_mode').value=f.usage_mode||'normalized';
  document.getElementById('usage_baseline_tokens').value=f.usage_baseline_tokens||0;
  const loc=window.location;
  document.getElementById('endpointUrl').textContent=loc.protocol+'//'+loc.host+'/v1';
  if(c.ui_lang && c.ui_lang!==LANG) setLang(c.ui_lang, false);
}

async function saveConfig(){
  const body={
    upstream_base_url:document.getElementById('upstream').value,
    model:document.getElementById('model').value,
    features:{
      tool_injection:document.getElementById('tool_injection').checked,
      strict_tool_names:document.getElementById('strict_tool_names').checked,
      flatten_tool_history:document.getElementById('flatten_tool_history').checked,
      strip_thinking:document.getElementById('strip_thinking').checked,
      enforce_max_tokens:document.getElementById('enforce_max_tokens').checked,
      enforce_stop_sequences:document.getElementById('enforce_stop_sequences').checked,
      compact_tool_schemas:document.getElementById('compact_tool_schemas').checked,
      ultra_compact_tools:document.getElementById('ultra_compact_tools').checked,
      max_history_messages:parseInt(document.getElementById('max_history_messages').value)||0,
      max_tool_result_chars:parseInt(document.getElementById('max_tool_result_chars').value)||0,
      max_output_tokens:parseInt(document.getElementById('max_output_tokens').value)||0,
      web_tools_enabled:document.getElementById('web_tools_enabled').checked,
      web_tools_max_iters:parseInt(document.getElementById('web_tools_max_iters').value)||4,
      usage_mode:document.getElementById('usage_mode').value,
      usage_baseline_tokens:parseInt(document.getElementById('usage_baseline_tokens').value)||0
    }
  };
  const key=document.getElementById('apikey').value;
  if(key && key.indexOf('...')===-1) body.api_key=key;
  // Collect fallback keys from the dynamic rows. Only overwrite the stored
  // list if the user supplied fresh keys (masked values still contain "...").
  const fbRows=[...document.querySelectorAll('#fbkeysWrap .fbrow input')]
    .map(i=>i.value.trim()).filter(v=>v.length>0);
  if(!fbRows.some(v=>v.indexOf('...')!==-1)){
    body.fallback_api_keys=fbRows;
  }
  await fetch('/admin/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const m=document.getElementById('savedMsg'); m.classList.add('show'); setTimeout(()=>m.classList.remove('show'),1500);
  loadConfig();
}

let LAST_LOGS=[];
function fmtTime(ts){ const d=new Date(ts*1000); return d.toLocaleTimeString(); }
function renderLogs(logs){
  LAST_LOGS=logs;
  const body=document.getElementById('logBody');
  if(!logs||!logs.length){ body.innerHTML='<tr><td colspan="7" class="empty">'+I18N[LANG].noLogs+'</td></tr>'; return; }
  body.innerHTML=logs.map(l=>{
    // Key-selection events (fallback logic): render a clear, dedicated row.
    if(l.key_used||l.key_rejected){
      const ok=!!l.key_used;
      const which=l.key_used||l.key_rejected;
      const tag=ok?'<span class="tag ok">key ok</span>':'<span class="tag err">key rejected</span>';
      const info=which+' '+(l.key_masked||'')+' (HTTP '+(l.status||'-')+')';
      return '<tr>'+
        '<td class="mono">'+fmtTime(l.ts)+'</td>'+
        '<td class="mono">-</td>'+
        '<td>'+tag+'</td>'+
        '<td class="mono">-</td>'+
        '<td class="mono">'+escapeHtml(info)+'</td>'+
        '<td class="mono">-</td>'+
        '<td class="mono">-</td>'+
      '</tr>';
    }
    let stop=l.stop_reason||(l.error?'error':'-');
    let tags='';
    if(l.error){ tags='<span class="tag err">error</span>'; }
    else{
      if(l.stream) tags+='<span class="tag stream">stream</span> ';
      if(l.tool_use) tags+='<span class="tag tool">tool</span> ';
      if(!l.tool_use && !l.error) tags+='<span class="tag ok">ok</span> ';
    }
    const usage=l.usage?(l.usage.input_tokens+' / '+l.usage.output_tokens):'-';
    // Token-source breakdown (where the input cost comes from).
    let bd='';
    if(l.breakdown && (l.breakdown.history_tok||l.breakdown.tools_tok)){
      const b=l.breakdown;
      bd='<div style="color:var(--muted);font-size:10.5px;margin-top:2px">'+
         'hist '+(b.history_tok||0)+' ('+(b.history_msgs||0)+'msg) · '+
         'tools '+(b.tools_tok||0)+' · sys '+(b.system_tok||0)+'</div>';
    }
    const toolsCol=(l.client_tools||0)+(l.tool_use?' →✓':'');
    return '<tr>'+
      '<td class="mono">'+fmtTime(l.ts)+'</td>'+
      '<td class="mono">'+(l.model||'-')+'</td>'+
      '<td>'+tags+'</td>'+
      '<td class="mono">'+toolsCol+'</td>'+
      '<td class="mono">'+(l.error?('<span style="color:var(--danger)">'+escapeHtml(String(l.error)).slice(0,40)+'</span>'):stop)+'</td>'+
      '<td class="mono">'+usage+bd+'</td>'+
      '<td class="mono">'+(l.latency_s!==undefined?l.latency_s+'s':'-')+'</td>'+
    '</tr>';
  }).join('');
}
function escapeHtml(s){return s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

async function pollLogs(){
  try{ const r=await fetch('/admin/logs'); const d=await r.json(); renderLogs(d.logs);
    document.getElementById('statusDot').className='dot on';
  }catch(e){ document.getElementById('statusDot').className='dot off'; }
}
async function clearLogs(){ await fetch('/admin/logs/clear',{method:'POST'}); pollLogs(); }
async function stopProxy(){
  var msg=(LANG==='ru')?'\u041e\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c \u043f\u0440\u043e\u043a\u0441\u0438? \u041f\u043e\u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f \u0440\u0443\u0447\u043d\u043e\u0439 \u043f\u0435\u0440\u0435\u0437\u0430\u043f\u0443\u0441\u043a.':'Stop the proxy? You will need to start it again manually.';
  if(!confirm(msg)) return;
  try{
    await fetch('/admin/shutdown',{method:'POST'});
  }catch(e){}
  document.getElementById('statusDot').className='dot off';
  var sm=document.getElementById('savedMsg');
  if(sm){ sm.style.opacity=1; sm.textContent=(LANG==='ru')?'\u041f\u0440\u043e\u043a\u0441\u0438 \u043e\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d':'Proxy stopped'; }
}

loadConfig();
pollLogs();
setInterval(pollLogs,2000);
</script>
</body>
</html>
"""
