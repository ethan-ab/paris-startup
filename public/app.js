/* Paris Startup Atlas: a 3D map of Paris where every building that hosts a startup is coloured by stage.
   Vanilla JS + MapLibre GL. Data: STARTUPS (rows) and BUILDINGS (footprints, one per occupied building). */
(() => {
'use strict';

const STAGE = {
  early:  {label:'Early stage', short:'Early',     hint:'under 50 people', hex:'#2a78d6', sel:'#1A56A0', hov:'#5B9AE3', c:'--early',  ci:'--early-ink',  cs:'--early-soft'},
  growth: {label:'Growth',      short:'Growth',    hint:'50 to 249',       hex:'#1baf7a', sel:'#0F7D56', hov:'#4CC497', c:'--growth', ci:'--growth-ink', cs:'--growth-soft'},
  scale:  {label:'Scale-up',    short:'Scale-ups', hint:'250 and more',    hex:'#eb6834', sel:'#B9461A', hov:'#F08A60', c:'--scale',  ci:'--scale-ink',  cs:'--scale-soft'},
};
const RANK = {early:0, growth:1, scale:2};
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fold = s => String(s ?? '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
const slug = n => fold(n).replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const nf = new Intl.NumberFormat('en-US');
const money = v => !v ? '' : v >= 1e9 ? `$${(v/1e9).toFixed(1)}B` : v >= 1e6 ? `$${(v/1e6).toFixed(v >= 1e8 ? 0 : 1)}M` : `$${Math.round(v/1e3)}K`;
const mobile = () => innerWidth <= 760;
const S = STARTUPS, B = BUILDINGS.features;
S.forEach((p, i) => { p.i = i; p._s = fold([p.n, p.d, p.s, p.a, p.area, p.cp, p.pc].join(' ')); p._slug = slug(p.n); });
const byB = new Map(B.map(f => [f.properties.b, f]));
const center = f => { const r = f.geometry.type === 'Polygon' ? f.geometry.coordinates[0] : f.geometry.coordinates[0][0];
  let x = 0, y = 0; r.forEach(c => { x += c[0]; y += c[1]; }); return [x / r.length, y / r.length]; };
B.forEach(f => { f._c = center(f); });

/* ---------- state ---------- */
const active = new Set(Object.keys(STAGE));
let query = '', fSec = '', showSeat = false, selB = null, openI = null, hovB = null;
const visible = p => active.has(p.st) && (showSeat || p.k === 'office') && (!fSec || p.s === fSec) && (!query || p._s.includes(query));
const bySize = (a, b) => (b.e || 0) - (a.e || 0) || a.n.localeCompare(b.n, 'fr');

/* ---------- base map: OpenFreeMap vector tiles, quieted down; neutral 3D buildings ---------- */
const PAL = {land:'#F2F1ED', park:'#DDE9D8', wood:'#E3EDDF', water:'#C9D9EE', road:'#FFFFFF', casing:'#E3E1DB', rail:'#DCD9D2',
  bldg:'#E4E2DD', label:'#7A7C82', minor:'#A3A5AB', waterLabel:'#7088B5'};
function quiet(style){
  style.layers = style.layers.filter(l => !/^(boundary|highway-shield|road_shield|airport|label_country|label_state|poi|aeroway)/.test(l.id) && l.source !== 'ne2_shaded');
  delete style.sources.ne2_shaded;
  style.layers = style.layers.map(l => {
    const p = l.paint = l.paint || {}, id = l.id;
    if (id === 'background') p['background-color'] = PAL.land;
    else if (id === 'park') { p['fill-color'] = PAL.park; p['fill-opacity'] = 1; }
    else if (id.startsWith('landcover')) { p['fill-color'] = PAL.wood; p['fill-opacity'] = .7; }
    else if (id.startsWith('landuse')) p['fill-opacity'] = 0;
    else if (id === 'water') p['fill-color'] = PAL.water;
    else if (id === 'waterway') p['line-color'] = PAL.water;
    else if (id === 'building') return {id:'building-3d', type:'fill-extrusion', source:'openmaptiles', 'source-layer':'building', minzoom:12.5,
      paint:{'fill-extrusion-color':PAL.bldg, 'fill-extrusion-opacity':['interpolate',['linear'],['zoom'],12.5,0,13.5,.92],
        'fill-extrusion-height':['coalesce',['get','render_height'],8], 'fill-extrusion-base':['coalesce',['get','render_min_height'],0]}};
    else if (l.type === 'line' && l['source-layer'] === 'transportation') p['line-color'] = /casing/.test(id) ? PAL.casing : /rail/.test(id) ? PAL.rail : PAL.road;
    else if (l.type === 'fill' && l['source-layer'] === 'transportation') p['fill-color'] = PAL.road;
    else if (l.type === 'symbol') {
      p['text-color'] = /water/.test(id) ? PAL.waterLabel : /other|minor|street|road|transportation/.test(id) ? PAL.minor : PAL.label;
      p['text-halo-color'] = PAL.land; p['text-halo-width'] = 1.2; p['text-halo-blur'] = 0;
    }
    return l;
  });
  return style;
}
/* Offline build (claude.ai artifact): water, parks and roads from data/basemap.json, central-Paris buildings from window.CONTEXT */
function offlineStyle(BM, CX){
  const src = k => ({type:'geojson', data:BM[k]});
  const byClass = (mw, pr, se, other) => ['interpolate',['exponential',1.6],['zoom'],
    10,['match',['get','r'],['motorway','trunk'],mw[0],'primary',pr[0],'secondary',se[0],other[0]],
    16,['match',['get','r'],['motorway','trunk'],mw[1],'primary',pr[1],'secondary',se[1],other[1]]];
  const ctx = {type:'FeatureCollection', features:(CX ? CX.b : []).map(a => {
    const ring = []; let x = 0, y = 0;
    for (let k = 1; k < a.length; k += 2) { x += a[k]; y += a[k+1]; ring.push([CX.x0 + x / 1e5, CX.y0 + y / 1e5]); }
    ring.push(ring[0]);
    return {type:'Feature', properties:{h:a[0]}, geometry:{type:'Polygon', coordinates:[ring]}};
  })};
  return {version:8, sources:{water:src('water'), park:src('park'), wood:src('wood'), road:src('road'), rail:src('rail'), ctx:{type:'geojson', data:ctx}}, layers:[
    {id:'background', type:'background', paint:{'background-color':PAL.land}},
    {id:'wood', type:'fill', source:'wood', paint:{'fill-color':PAL.wood, 'fill-opacity':.8}},
    {id:'park', type:'fill', source:'park', paint:{'fill-color':PAL.park}},
    {id:'water', type:'fill', source:'water', paint:{'fill-color':PAL.water}},
    {id:'rail', type:'line', source:'rail', paint:{'line-color':PAL.rail, 'line-width':['interpolate',['exponential',1.6],['zoom'],10,.4,16,1.6]}},
    {id:'road-casing', type:'line', source:'road', layout:{'line-cap':'round','line-join':'round'}, paint:{'line-color':PAL.casing, 'line-width':byClass([2.4,16],[2,14],[1.5,12],[.8,9])}},
    {id:'road', type:'line', source:'road', layout:{'line-cap':'round','line-join':'round'}, paint:{'line-color':PAL.road, 'line-width':byClass([1.6,13],[1.3,11],[1,9],[.4,6.5])}},
    {id:'building-3d', type:'fill-extrusion', source:'ctx', minzoom:12.5, paint:{'fill-extrusion-color':PAL.bldg,
      'fill-extrusion-opacity':['interpolate',['linear'],['zoom'],12.5,0,13.5,.92], 'fill-extrusion-height':['get','h']}},
  ]};
}

const HOME = () => mobile() ? {center:[2.3440,48.8655], zoom:13.9, pitch:55, bearing:-20} : {center:[2.3470,48.8668], zoom:14.6, pitch:58, bearing:-22};
const map = new maplibregl.Map({container:'map',
  style:{version:8, sources:{}, layers:[{id:'bg', type:'background', paint:{'background-color':PAL.land}}]},
  ...HOME(), minZoom:10.5, maxZoom:18.6, maxPitch:72, maxBounds:[[2.0,48.70],[2.70,49.02]], attributionControl:{compact:true}});
map.touchZoomRotate.disableRotation();
let ready = false;

const COLOR = k => ['match',['coalesce',['feature-state','st'],['get','st']],'scale',STAGE.scale[k],'growth',STAGE.growth[k],STAGE.early[k]];
const ptsData = () => ({type:'FeatureCollection', features:S.filter(visible).map(p => ({type:'Feature', properties:{w:RANK[p.st] + 1}, geometry:{type:'Point', coordinates:[p.ll[1], p.ll[0]]}}))});
const styleReady = window.BASEMAP ? Promise.resolve(null) : fetch('https://tiles.openfreemap.org/styles/positron').then(r => r.json());
styleReady.then(st => {
  map.once('style.load', () => { if (ready) return;
    map.setLight({anchor:'viewport', color:'#ffffff', intensity:.32, position:[1.4, 210, 40]});
    /* city-scale density (fades out as buildings appear) */
    map.addSource('pts', {type:'geojson', data:ptsData()});
    map.addLayer({id:'density', type:'heatmap', source:'pts', maxzoom:14,
      paint:{'heatmap-weight':['get','w'], 'heatmap-intensity':['interpolate',['linear'],['zoom'],10,.5,13,1.2],
        'heatmap-radius':['interpolate',['linear'],['zoom'],10,6,13,18],
        'heatmap-opacity':['interpolate',['linear'],['zoom'],11,.75,13.4,0],
        'heatmap-color':['interpolate',['linear'],['heatmap-density'],0,'rgba(42,120,214,0)',.15,'rgba(42,120,214,.25)',.45,'rgba(42,120,214,.55)',.8,'rgba(235,104,52,.7)',1,'rgba(235,104,52,.9)']}});
    /* the startup buildings */
    map.addSource('sb', {type:'geojson', data:BUILDINGS, promoteId:'b'});
    map.addLayer({id:'sb-3d', type:'fill-extrusion', source:'sb', minzoom:11,
      paint:{'fill-extrusion-color':['case',['boolean',['feature-state','sel'],false],COLOR('sel'),['boolean',['feature-state','hov'],false],COLOR('hov'),COLOR('hex')],
        'fill-extrusion-height':['get','h'], 'fill-extrusion-base':['get','mh'],
        'fill-extrusion-opacity':['interpolate',['linear'],['zoom'],11,.6,13,.97]}});
    ready = true; apply();
    const h = decodeURIComponent(location.hash.slice(1)); const hp = h && S.find(p => p._slug === h);
    if (hp) { if (hp.k !== 'office') { showSeat = true; $('seat').checked = true; apply(); } pick(hp.i); }
  });
  map.setStyle(st ? quiet(st) : offlineStyle(window.BASEMAP, window.CONTEXT), {diff:false});
}).catch(() => { document.body.insertAdjacentHTML('beforeend', '<p style="position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:9;background:#fff;padding:8px 12px;border-radius:8px;font:13px var(--sans)">The map tiles did not load. Search still works.</p>'); });

/* ---------- filtering: which buildings show, and which stage colours each one ---------- */
let visB = [];
function apply(){
  const ids = [];
  for (const f of B) {
    let lead = null;
    for (const i of f.properties.s) { const p = S[i]; if (visible(p) && (!lead || RANK[p.st] > RANK[lead.st] || (RANK[p.st] === RANK[lead.st] && (p.e || 0) > (lead.e || 0)))) lead = p; }
    if (lead) { ids.push(f.properties.b); if (ready) map.setFeatureState({source:'sb', id:f.properties.b}, {st:lead.st}); }
  }
  visB = ids;
  if (ready) { map.setFilter('sb-3d', ['in', ['get','b'], ['literal', ids]]); map.getSource('pts').setData(ptsData()); }
  renderPanel();
  if (selB !== null && !ids.includes(selB)) closeCard(); else if (selB !== null) showCard(selB, openI, false);
}

/* ---------- panel ---------- */
const offices = S.filter(p => p.k === 'office');
$('stages').innerHTML = Object.entries(STAGE).map(([k, C]) =>
  `<button class="pill" type="button" data-k="${k}" aria-pressed="true" style="--c:var(${C.c})" title="${C.label}: ${C.hint}"><i></i>${C.short} <em>${nf.format(offices.filter(p => p.st === k).length)}</em></button>`).join('');
$('stages').querySelectorAll('.pill').forEach(b => b.onclick = () => {
  const k = b.dataset.k, only = active.size === 1 && active.has(k);
  if (only) Object.keys(STAGE).forEach(x => active.add(x)); else { active.clear(); active.add(k); }
  $('stages').querySelectorAll('.pill').forEach(x => x.setAttribute('aria-pressed', active.has(x.dataset.k)));
  shown = 40; apply();
});
const secs = [...new Set(S.map(p => p.s))].sort((a, b) => offices.filter(p => p.s === b).length - offices.filter(p => p.s === a).length);
secs.forEach(v => { const o = document.createElement('option'); o.value = v; o.textContent = `${v} (${nf.format(offices.filter(p => p.s === v).length)})`; $('fSec').appendChild(o); });
$('fSec').onchange = e => { fSec = e.target.value; shown = 40; apply(); if (fSec) fitVisible(); };
$('seat').onchange = e => { showSeat = e.target.checked; apply(); };
let qt; $('q').addEventListener('input', e => { clearTimeout(qt); qt = setTimeout(() => { query = fold(e.target.value.trim()); listOpen = !!query || listOpen; shown = 40; apply(); }, 120); });
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') { const r = $('results').querySelector('.res'); r && r.click(); } });
let listOpen = false, shown = 40;
$('browse').onclick = () => { listOpen = !listOpen; renderPanel(); };
$('legend').innerHTML = Object.values(STAGE).map(C => `<span><i style="--c:var(${C.c})"></i>${C.label}</span>`).join('') + `<span>Colour = stage of the largest startup in the building</span>`;

function renderPanel(){
  const vis = S.filter(visible);
  const nb = visB.length;
  $('total').textContent = `${nf.format(vis.length)} startups · ${nf.format(nb)} buildings`;
  $('browse').textContent = listOpen ? 'Hide the list' : 'Browse the list';
  const R = $('results');
  if (!listOpen) { R.hidden = true; return; }
  R.hidden = false;
  vis.sort(bySize);
  R.innerHTML = (vis.length ? vis.slice(0, shown).map(p => `<button class="res${p.i === openI ? ' on' : ''}" type="button" data-i="${p.i}" style="--c:var(${STAGE[p.st].c})">
      <i class="${p.k === 'office' ? '' : 'u'}"></i><div><b>${esc(p.n)}</b><span>${esc([p.s, p.cp || p.area].filter(Boolean).join(' · '))}</span></div><small>${p.e ? nf.format(p.e) : ''}</small></button>`).join('')
    : `<div class="none">No startup matches. Try another word or clear a filter.</div>`) +
    (vis.length > shown ? `<button class="more" type="button">Show ${nf.format(Math.min(200, vis.length - shown))} more</button>` : '');
  R.querySelectorAll('.res').forEach(b => b.onclick = () => { pick(+b.dataset.i); if (mobile()) { listOpen = false; renderPanel(); } });
  const m = R.querySelector('.more'); if (m) m.onclick = () => { shown += 200; renderPanel(); };
}

/* ---------- card: one building, the startups inside ---------- */
const card = $('card');
const VIA = {website:'its own website', linkedin:'its LinkedIn page', register:'the company register', research:'press and job listings'};
function detail(p){
  const C = STAGE[p.st];
  const host = (() => { try { return new URL(p.u).hostname.replace(/^www\./, ''); } catch { return ''; } })();
  const rows = [['Founded', p.y], ['Team', p.e ? nf.format(p.e) + ' people' : ''], ['Raised', money(p.f)], ['Last round', p.rd]].filter(r => r[1]);
  const where = [p.a, p.pc && (/^75/.test(p.pc) ? `${p.pc} Paris` : p.pc)].filter(Boolean).join(', ');
  return `<span class="tag" style="--ci:var(${C.ci});--cs:var(${C.cs})">${C.label}</span>
    ${p.d ? `<p>${esc(p.d)}${p.d.length >= 259 ? '…' : ''}</p>` : ''}
    ${rows.length ? `<dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl>` : ''}
    <div class="src ${p.k === 'office' ? '' : 'warn'}">${p.k === 'office' ? `Office at ${esc(where)}, from ${esc(VIA[p.v] || p.v)}.` : `Registered address only (${esc(where)}). We could not confirm the team works here.`}</div>
    <div class="links">${/^https?:\/\//i.test(p.u || '') ? `<a href="${esc(p.u)}" target="_blank" rel="noopener">${esc(host)} ↗</a>` : ''}
      <a href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(p.n + ', ' + where)}" target="_blank" rel="noopener">Directions ↗</a></div>`;
}
function showCard(b, i, fly){
  const f = byB.get(b); if (!f) return;
  const all = f.properties.s.map(k => S[k]);
  const list = all.filter(visible).sort(bySize);
  if (!list.length) return closeCard();
  if (i == null && list.length === 1) i = list[0].i;
  openI = i;
  const lead = list[0];
  const addr = (() => { const c = {}; list.forEach(p => { if (p.a) c[p.a] = (c[p.a] || 0) + 1; }); return Object.entries(c).sort((x, y) => y[1] - x[1])[0]?.[0] || ''; })();
  const place = lead.cp || [addr, lead.area].filter(Boolean).join(' · ');
  const counts = Object.keys(STAGE).map(k => [k, list.filter(p => p.st === k).length]).filter(x => x[1]);
  card.innerHTML = `<div class="ch"><div class="eyebrow">${esc(place)}</div>
      <h2>${list.length === 1 ? esc(lead.n) : `${nf.format(list.length)} startups in this building`}</h2>
      ${list.length > 1 ? `<div class="sbar">${counts.map(([k, n]) => `<i style="--c:var(${STAGE[k].c});flex-grow:${n}" title="${STAGE[k].label}: ${n}"></i>`).join('')}</div>` : ''}
      <button class="x" type="button" aria-label="Close">×</button></div>
    ${list.map(p => `<div class="co"><button type="button" data-i="${p.i}" aria-expanded="${p.i === i}" style="--c:var(${STAGE[p.st].c})">
        <i class="${p.k === 'office' ? '' : 'u'}"></i><div><b>${esc(p.n)}</b><span>${esc(p.s)}</span></div><small>${p.e ? nf.format(p.e) : ''}</small></button>
        <div class="det" ${p.i === i ? '' : 'hidden'}>${p.i === i ? detail(p) : ''}</div></div>`).join('')}`;
  card.hidden = false;
  card.querySelector('.x').onclick = closeCard;
  card.querySelectorAll('.co>button').forEach(btn => btn.onclick = () => {
    const k = +btn.dataset.i, d = btn.nextElementSibling, open = d.hidden;
    card.querySelectorAll('.det').forEach(x => { x.hidden = true; x.previousElementSibling.setAttribute('aria-expanded', 'false'); });
    if (open) { d.innerHTML = detail(S[k]); d.hidden = false; btn.setAttribute('aria-expanded', 'true'); openI = k; setHash(S[k]); } else { openI = null; }
  });
  if (i != null) { const el = card.querySelector(`.co>button[data-i="${i}"]`); el && list.length > 6 && el.scrollIntoView({block:'center'}); }
  if (selB !== null && selB !== b && ready) map.setFeatureState({source:'sb', id:selB}, {sel:false});
  selB = b; if (ready) map.setFeatureState({source:'sb', id:b}, {sel:true});
  setHash(i != null ? S[i] : lead);
  if (fly) {
    const z = Math.max(map.getZoom(), lead.cp === 'Station F' ? 16.4 : 16.6);
    map.flyTo({center:f._c, zoom:z, pitch:Math.max(map.getPitch(), 50), duration:1300, essential:true,
      padding:mobile() ? {top:0, bottom:card.offsetHeight, left:0, right:0} : {top:0, bottom:0, left:360, right:380}});
  }
}
function setHash(p){ try { history.replaceState(null, '', p ? '#' + p._slug : location.pathname + location.search); } catch {} }
function closeCard(){
  card.hidden = true; card.innerHTML = '';
  if (selB !== null && ready) map.setFeatureState({source:'sb', id:selB}, {sel:false});
  selB = null; openI = null; setHash(null); renderPanel();
}
function pick(i){ const p = S[i]; if (p.b == null) return; showCard(p.b, i, true); renderPanel(); }
function fitVisible(){
  const b = new maplibregl.LngLatBounds(); let n = 0;
  for (const id of visB) { b.extend(byB.get(id)._c); n++; }
  if (n) map.fitBounds(b, {padding:mobile() ? 40 : {top:60, bottom:60, left:380, right:60}, maxZoom:16.5, duration:900});
}

/* ---------- map interaction ---------- */
const tip = $('tip'), coarse = matchMedia('(pointer:coarse)').matches;
function bAt(pt, r){
  if (!ready) return null;
  const fs = map.queryRenderedFeatures([[pt.x - r, pt.y - r], [pt.x + r, pt.y + r]], {layers:['sb-3d']});
  return fs.length ? fs[0].properties.b : null;
}
map.on('click', e => { const b = bAt(e.point, coarse ? 10 : 3); if (b != null) { showCard(b, null, false); renderPanel(); } else if (selB !== null) closeCard(); });
map.on('mousemove', e => {
  const b = coarse ? null : bAt(e.point, 2);
  if (b !== hovB) { if (hovB !== null) map.setFeatureState({source:'sb', id:hovB}, {hov:false}); hovB = b; if (b !== null) map.setFeatureState({source:'sb', id:b}, {hov:true}); }
  map.getCanvas().style.cursor = b !== null ? 'pointer' : '';
  if (b === null) { tip.hidden = true; return; }
  const list = byB.get(b).properties.s.map(k => S[k]).filter(visible).sort(bySize);
  tip.innerHTML = list.length === 1 ? `${esc(list[0].n)}<span>${esc(list[0].s)}</span>` : `${esc(list[0].cp || list.slice(0, 2).map(p => p.n).join(', '))}<span>${nf.format(list.length)} startups</span>`;
  tip.style.left = e.originalEvent.clientX + 'px'; tip.style.top = e.originalEvent.clientY + 'px'; tip.hidden = false;
});
map.on('mouseout', () => { tip.hidden = true; });
map.on('movestart', () => { tip.hidden = true; });
$('zin').onclick = () => map.zoomIn({duration:300});
$('zout').onclick = () => map.zoomOut({duration:300});
$('tilt').onclick = () => { const flat = map.getPitch() > 5; map.easeTo({pitch:flat ? 0 : 58, bearing:flat ? 0 : -22, duration:700}); $('tilt').textContent = flat ? '3D' : '2D'; };
$('home').onclick = () => { closeCard(); map.flyTo({...HOME(), duration:1100}); $('tilt').textContent = '2D'; };
addEventListener('keydown', e => { if (e.key === 'Escape') closeCard(); });

apply();

/* Read-only hooks for end-to-end tests. */
window.__psa = {map, startups:S, buildings:B, isReady:() => ready, visibleBuildings:() => visB.length, selected:() => selB};
})();
