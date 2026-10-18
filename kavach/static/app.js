const $ = id => document.getElementById(id);
const KEY = new URLSearchParams(location.hash.slice(1)).get('key') || '';
const esc = v => String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const inr = n => '₹' + Math.round(n).toLocaleString('en-IN');
const inrShort = n => n >= 1e7 ? '₹' + (n/1e7).toFixed(2) + ' Cr' : n >= 1e5 ? '₹' + (n/1e5).toFixed(1) + ' L' : inr(n);
const pct = x => (x*100).toFixed(1) + '%';
const riskColor = r => r >= .8 ? 'var(--hold)' : r >= .3 ? 'var(--step)' : 'var(--allow)';
const clock = ts => { const d = Math.floor(ts/86400), s = ts % 86400; return `Day ${d+1} ${String(Math.floor(s/3600)).padStart(2,'0')}:${String(Math.floor(s%3600/60)).padStart(2,'0')}`; };
const store = new Map();

// ---- mule graph ------------------------------------------------------
const cy = cytoscape({
  container: $('cy'),
  style: [
    {selector:'node', style:{'background-color':'#4c8dff','width':14,'height':14,'label':'data(id)','color':'#8a96ad','font-size':8,'font-family':'JetBrains Mono','text-valign':'bottom','text-margin-y':3}},
    {selector:'node.suspect', style:{'background-color':'#ff4d5e','width':20,'height':20}},
    {selector:'node.mule', style:{'background-color':'#a07cff','width':28,'height':28,'border-width':3,'border-color':'#d9c9ff','color':'#d9c9ff','font-size':10}},
    {selector:'edge', style:{'width':'mapData(amt, 0, 150000, 1, 6)','line-color':'#ff4d5e','opacity':.55,'curve-style':'bezier','target-arrow-shape':'triangle','target-arrow-color':'#ff4d5e','arrow-scale':.7}},
    {selector:'edge.step', style:{'line-color':'#ffb020','target-arrow-color':'#ffb020','line-style':'dashed'}},
  ]
});
let layoutTimer = null;
function relayout(){ clearTimeout(layoutTimer); layoutTimer = setTimeout(() => cy.layout({name:'cose', animate:false, nodeRepulsion:6000, idealEdgeLength:50, randomize:false}).run(), 400); }
function addEdge(t){
  for (const id of [t.sender, t.receiver]) if (!cy.$id(id).length) cy.add({data:{id}, position:{x:Math.random()*400, y:Math.random()*300}});
  cy.$id(t.receiver).addClass('suspect');
  cy.add({data:{id:'e'+t.txn_id, source:t.sender, target:t.receiver, amt:t.amount}, classes: t.decision === 'STEP_UP' ? 'step' : ''});
  if (cy.edges().length > 260) cy.remove(cy.edges()[0]);
  cy.nodes().filter(n => n.degree() === 0).remove();
  relayout();
}

// ---- feed ------------------------------------------------------------
function row(t){
  store.set(t.txn_id, t);
  if (store.size > 2000) store.delete(store.keys().next().value);
  const truth = t.label == null ? '' : t.label == 1 ? `<span class="truth" style="color:#ff8a96">${(t.kind||'fraud').replace('_',' ')}</span>`
    : 'XC'.includes(t.sender[0]) ? '<span class="truth" style="color:#c9b4ff">mule acct</span>' : '<span class="truth muted">legit</span>';
  const el = document.createElement('div');
  el.className = 'row' + (t.manual ? ' manual' : '');
  el.dataset.id = t.txn_id;
  el.innerHTML = `<span class="dec ${t.decision}">${t.decision.replace('_','-')}</span><span class="hide-s muted">${clock(t.ts).slice(6)}</span>
    <span>${esc(t.sender)} → ${esc(t.receiver)}</span><span style="text-align:right">${inr(t.amount)}</span>
    <span class="hide-s"><span class="risk"><i style="width:${t.risk*100}%;background:${riskColor(t.risk)}"></i></span></span><span class="hide-s">${truth}</span>`;
  if (feedHover) { held.push(el); return; }
  const f = $('feed'); f.prepend(el);
  while (f.children.length > 150) f.lastChild.remove();
}
// freeze the feed while the pointer is over it, so rows can be clicked
let feedHover = false, held = [];
$('feed').addEventListener('mouseenter', () => { feedHover = true; $('feedhint').textContent = 'paused while hovering'; });
$('feed').addEventListener('mouseleave', () => {
  feedHover = false; $('feedhint').textContent = HINT;
  const f = $('feed'); held.slice(-150).forEach(el => f.prepend(el)); held = [];
  while (f.children.length > 150) f.lastChild.remove();
});
const HINT = 'hover to freeze · click a row for reasons';
$('feed').addEventListener('click', e => { const r = e.target.closest('.row'); if (r) showDrawer(store.get(r.dataset.id)); });

function reasonsHtml(t){
  if (!t.reasons || !t.reasons.length) return '<div class="muted" style="font-size:12px;margin-top:6px">No risk drivers - normal behaviour for this account.</div>';
  return t.reasons.map(r => `<div class="reason"><span>${esc(r.label)}</span>${r.impact == null ? '<span style="color:var(--violet);font-size:11px">network / policy</span>' : `<span class="bar"><i style="width:${Math.min(100, r.impact*100)}%"></i></span>`}</div>`).join('');
}
const ACTION = {HOLD:'Payment held · victim alerted · bank notified', STEP_UP:'OTP + 30-min cooling-off with scam warning', ALLOW:'Approved'};
function showDrawer(t){
  if (!t) return;
  const d = $('drawer');
  d.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center"><span class="dec ${t.decision}">${t.decision}</span><button id="drawer-close" aria-label="Close">✕</button></div>
    <div style="font:600 15px var(--mono);margin:10px 0 2px">${esc(t.sender)} → ${esc(t.receiver)} · ${inr(t.amount)}</div>
    <div class="muted" style="font-size:12px">${clock(t.ts)} · risk ${(t.risk*100).toFixed(1)}% · scored in ${t.latency_ms} ms</div>
    <div style="margin-top:10px;font-size:12px">${ACTION[t.decision]}</div>
    <div style="margin-top:10px;font-size:11px;color:var(--muted);text-transform:uppercase">Top risk drivers</div>${reasonsHtml(t)}
    ${t.label != null ? `<div class="muted" style="font-size:11px;margin-top:10px">Ground truth: ${t.label == 1 ? t.kind : 'legit'}</div>` : ''}`;
  d.classList.add('show');
  $('drawer-close').addEventListener('click', () => d.classList.remove('show'));
}

// ---- stats -----------------------------------------------------------
function stats(m){
  const s = m.stats;
  $('k-scored').textContent = s.scored.toLocaleString('en-IN');
  $('k-hold').textContent = s.hold.toLocaleString('en-IN');
  $('k-step').textContent = s.step_up.toLocaleString('en-IN');
  $('k-saved').textContent = inrShort(s.protected);
  $('k-saved-s').textContent = 'of ' + inrShort(s.fraud_amount) + ' scammed from victims';
  $('k-recall').textContent = s.fraud ? pct(s.caught / s.fraud) : '–';
  $('k-recall-s').textContent = `${s.caught} / ${s.fraud} frauds`;
  const legit = s.scored - s.fraud;
  $('k-fp').textContent = legit ? (100 * s.false_alarm / legit).toFixed(2) + '%' : '–';
  $('k-watch').textContent = m.watchlist;
  if (m.latency.p50_ms != null) $('k-lat').textContent = `${(m.latency.p50_ms*1000).toFixed(0)}/${(m.latency.p99_ms*1000).toFixed(0)}µs`;
  $('clock').textContent = clock(m.sim_ts);
  $('pause').textContent = m.paused ? 'Resume' : 'Pause';
}

function hello(m){
  const a = m.accelerator;
  $('arch').textContent = a.arch === 's390x' ? 'IBM LinuxONE · s390x' : 'arch ' + a.arch;
  $('eng').textContent = a.on_chip_ai ? 'Telum on-chip AI · zDNN' : a.engine;
  $('eng').classList.toggle('on', a.on_chip_ai);
  const ab = m.metrics['ablation_recall_at_0.1pct_fpr'];
  const rows = [['Rule: >₹20k to new payee', ab['rule_amount>20k_and_new_payee'].recall, '#5b6780', `at ${pct(ab['rule_amount>20k_and_new_payee'].fpr)} FPR`],
                ['Per-transaction ML', ab.per_txn_model_only.recall, '#4c8dff'],
                ['Kavach (+ mule graph + calls)', ab.kavach_full.recall, '#2ecc8f']];
  $('abl').innerHTML = rows.map(([n, v, c, note]) => `<div class="r"><span>${n}${note ? `<br><span class="muted" style="font-size:10px">${note}</span>` : ''}</span><span class="b"><i style="width:${v*100}%;background:${c}"></i></span><b style="font-family:var(--mono)">${pct(v)}</b></div>`).join('')
    + `<div class="muted" style="font-size:11px">PR-AUC ${m.metrics.pr_auc.toFixed(3)} · ${m.metrics.n_test_fraud} frauds in ${m.metrics.n_test_txns.toLocaleString('en-IN')} unseen txns</div>`;
}

function call(c){
  const el = document.createElement('div');
  el.className = 'call';
  el.innerHTML = `<b>${esc(c.user)}</b> <span class="muted">${clock(c.ts)} · scam risk ${pct(c.risk)}${c.manual ? ' · live' : ''}</span><div style="margin-top:3px">“${esc(c.transcript.slice(0, 150))}${c.transcript.length > 150 ? '…' : ''}”</div>`;
  $('calls').prepend(el);
  while ($('calls').children.length > 40) $('calls').lastChild.remove();
}

// ---- socket ----------------------------------------------------------
function connect(){
  const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  ws.onopen = () => { if (KEY) ws.send(JSON.stringify({key: KEY})); };
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.type === 'hello') hello(m);
    else if (m.type === 'stats') stats(m);
    else if (m.type === 'call') call(m);
    else if (m.type === 'txn') {
      row(m);
      if (m.decision !== 'ALLOW') addEdge(m);
      if (m.new_watchlist) cy.$id(m.new_watchlist).addClass('mule');
    }
  };
  ws.onclose = e => {
    if (e.code === 1008) {  // server requires an API key: say so instead of retrying forever
      $('feed').innerHTML = '<div style="padding:16px;color:var(--step)">Locked: this server requires an API key. Open the dashboard as <b>/#key=&lt;your key&gt;</b>.</div>';
      return;
    }
    setTimeout(connect, 1500);
  };
}
connect();

const post = async (url, body) => {
  const headers = {'Content-Type':'application/json'};
  if (KEY) headers['X-API-Key'] = KEY;
  const r = await fetch(url, {method:'POST', headers, body: JSON.stringify(body)});
  const j = await r.json();
  if (!r.ok) { const res = $('d-res'); res.classList.add('show'); const msg = typeof j.detail === 'string' ? j.detail : (j.detail && j.detail[0] && j.detail[0].msg) || r.status;
    res.innerHTML = `<div style="color:var(--hold)">Request rejected: ${esc(msg)}</div>`; throw new Error('bad input'); }
  return j;
};
$('pause').onclick = () => post('/api/control', {paused: $('pause').textContent === 'Pause'}).then(stats);
$('speed').onchange = e => post('/api/control', {speed: +e.target.value});
$('d-call').onclick = async () => {
  const r = await post('/api/call', {user: $('d-user').value.trim(), transcript: $('d-text').value});
  const res = $('d-res'); res.classList.add('show');
  res.innerHTML = `<div class="muted" style="font-size:11px">CALL ANALYSIS</div><div class="big" style="color:${r.scam ? 'var(--hold)' : 'var(--allow)'}">${r.scam ? 'Likely scam' : 'Looks normal'} · ${pct(r.risk)}</div><div class="muted" style="font-size:12px;margin-top:4px">${r.scam ? 'Risk attached to ' + esc(r.user) + ' for the next 2 hours. Now try the payment.' : 'No scam pattern found.'}</div>`;
};
$('d-pay').onclick = async () => {
  const t = await post('/api/txn', {sender: $('d-user').value.trim(), receiver: $('d-recv').value.trim(), amount: +$('d-amt').value});
  const res = $('d-res'); res.classList.add('show');
  res.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center"><span class="dec ${t.decision}" style="font-size:13px;padding:4px 10px">${t.decision}</span><span class="muted" style="font:12px var(--mono)">${t.latency_ms} ms</span></div>
    <div class="big" style="margin-top:8px">${pct(t.risk)} risk</div><div style="font-size:12px;margin-top:2px">${ACTION[t.decision]}</div>${reasonsHtml(t)}`;
};
