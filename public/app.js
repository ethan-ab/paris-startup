/* Paris Startup Atlas: a 3D map of Paris where every building that hosts a startup is coloured by stage.
   Vanilla JS + MapLibre GL. Data: STARTUPS (rows) and BUILDINGS (footprints, one per occupied building). */
(() => {
'use strict';

const STAGE = {
  early:  {label:'Early stage', short:'Early',     hint:'under 50 people', hex:'#2a78d6', sel:'#1A56A0', hov:'#5B9AE3', c:'--early',  ci:'--early-ink',  cs:'--early-soft'},
  growth: {label:'Growth',      short:'Growth',    hint:'50 to 249',       hex:'#1baf7a', sel:'#0F7D56', hov:'#4CC497', c:'--growth', ci:'--growth-ink', cs:'--growth-soft'},
  scale:  {label:'Scale-up',    short:'Scale-ups', hint:'250 and more',    hex:'#eb6834', sel:'#B9461A', hov:'#F08A60', c:'--scale',  ci:'--scale-ink',  cs:'--scale-soft'},
};
const UNKNOWN = {label:'Size unknown', hex:'#A9ABB1', sel:'#7E8087', hov:'#C2C4C9', c:'--unknown', ci:'--muted', cs:'--hover'};
const ST = k => STAGE[k] || UNKNOWN;
const RANK = {unknown:-1, early:0, growth:1, scale:2};
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fold = s => String(s ?? '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
const slug = n => fold(n).replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const nf = new Intl.NumberFormat('en-US');
const money = v => !v ? '' : v >= 1e9 ? `$${(v/1e9).toFixed(1)}B` : v >= 1e6 ? `$${(v/1e6).toFixed(v >= 1e8 ? 0 : 1)}M` : `$${Math.round(v/1e3)}K`;
const mobile = () => innerWidth <= 760;
/* logos: self-hosted plates (scripts/logos.py) or, in the offline artifact, inlined data URIs; a monogram sits underneath */
const LOGOS = window.LOGOS || null;
const logoSrc = k => !k ? '' : LOGOS ? (LOGOS[k] || '') : `logos/${k}.webp`;
const initials = n => { const w = String(n).replace(/[^\p{L}\p{N}]+/gu, ' ').trim().split(' ').filter(Boolean);
  return ((w[0] || '?')[0] + (w.length > 1 ? w[1][0] : (w[0] || '')[1] || '')).toUpperCase(); };
const logo = (p, size) => { const C = ST(p.st), src = logoSrc(p.lg);
  return `<span class="lg lg-${size}${p.k === 'office' ? '' : ' u'}" style="--c:var(${C.c});--cs:var(${C.cs});--ci:var(${C.ci})" aria-hidden="true"><b>${esc(initials(p.n))}</b>${
    src ? `<img src="${esc(src)}" alt="" loading="lazy" decoding="async">` : ''}</span>`; };
const wireLogos = root => root.querySelectorAll('.lg img').forEach(im => { im.onerror = () => im.remove(); if (im.complete && !im.naturalWidth) im.remove(); });
const S = STARTUPS, B = BUILDINGS.features;
const SFTXT = {here:'station f', moved:'left station f alumni moved', past:'station f alumni'};
S.forEach((p, i) => { p.i = i; p._s = fold([p.n, p.d, p.s, p.a, p.area, p.cp, p.pc, p.sf && SFTXT[p.sf.s], p.sf && p.sf.p].join(' ')); p._slug = slug(p.n); });
const LEFT_SF = S.filter(p => p.sf && p.sf.s === 'moved').length;
const byB = new Map(B.map(f => [f.properties.b, f]));
const center = f => { const r = f.geometry.type === 'Polygon' ? f.geometry.coordinates[0] : f.geometry.coordinates[0][0];
  let x = 0, y = 0; r.forEach(c => { x += c[0]; y += c[1]; }); return [x / r.length, y / r.length]; };
B.forEach(f => { f._c = center(f); });

/* ---------- startups a visitor added: kept in this browser (localStorage) until reviewed ---------- */
const STORE = 'psa.added.v1';
const AREA = {}; S.forEach(p => { if (p.pc && p.area && !AREA[p.pc]) AREA[p.pc] = p.area; });
const ZONE = [2.15, 48.78, 2.52, 48.94]; // Paris and the inner suburbs, lng/lat
const inZone = (lat, lng) => lng >= ZONE[0] && lng <= ZONE[2] && lat >= ZONE[1] && lat <= ZONE[3];
const stageOf = e => !e ? 'unknown' : e < 50 ? 'early' : e < 250 ? 'growth' : 'scale';
const str = (v, max) => typeof v === 'string' ? v.trim().slice(0, max) : '';
const int = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi ? v : undefined;
const okRing = r => Array.isArray(r) && r.length >= 4 && r.length <= 400 && r.every(c => Array.isArray(c) && c.length === 2 && c.every(Number.isFinite));
function cleanMine(r){
  if (!r || !Array.isArray(r.ll) || r.ll.length !== 2 || !r.ll.every(Number.isFinite) || !inZone(r.ll[0], r.ll[1])) return null;
  const secs = new Set(S.filter(q => !q.mine).map(q => q.s));
  const p = {n:str(r.n, 80), u:str(r.u, 300), d:str(r.d, 250), s:secs.has(r.s) ? r.s : '', a:str(r.a, 200), pc:str(r.pc, 10), area:str(r.area, 60),
    ll:[r.ll[0], r.ll[1]], k:'office', t:Number.isFinite(r.t) ? r.t : 0, mine:true};
  const e = int(r.e, 1, 100000), y = int(r.y, 1990, 2100);
  if (e) p.e = e; if (y) p.y = y;
  p.st = stageOf(p.e);
  if (r.fp && okRing(r.fp.ring)) p.fp = {ring:r.fp.ring, h:Number.isFinite(r.fp.h) ? Math.min(Math.max(r.fp.h, 3), 300) : 15};
  return p.n.length >= 2 && p.s ? p : null;
}
/* point in polygon (outer rings), lng/lat */
function inRing(x, y, ring){ let c = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const [xi, yi] = ring[i], [xj, yj] = ring[j];
  if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c; } return c; }
const polys = g => g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [];
const contains = (g, ll) => polys(g).some(pg => inRing(ll[1], ll[0], pg[0]));
let nextB = B.reduce((m, f) => Math.max(m, f.properties.b), -1) + 1;
/* attach a startup to a building: one that already hosts startups, else the footprint we found for it, else a small block */
function house(p){
  let f = B.find(f => !f.gone && contains(f.geometry, p.ll));
  if (!f) {
    const lat = p.ll[0], lng = p.ll[1], dy = 9 / 111320, dx = 9 / (111320 * Math.cos(lat * Math.PI / 180));
    if (!p.fp) p.fp = {ring:[[lng-dx,lat-dy],[lng+dx,lat-dy],[lng+dx,lat+dy],[lng-dx,lat+dy],[lng-dx,lat-dy]].map(c => c.map(v => +v.toFixed(6))), h:18};
    f = {type:'Feature', properties:{b:nextB++, h:p.fp.h, mh:0, s:[]}, geometry:{type:'Polygon', coordinates:[p.fp.ring]}, mine:true};
    f._c = center(f); B.push(f); byB.set(f.properties.b, f);
  }
  f.properties.s.push(p.i); p.b = f.properties.b;
}
const indexOne = p => { p._s = fold([p.n, p.d, p.s, p.a, p.area, p.cp, p.pc, p.sf && SFTXT[p.sf.s], p.sf && p.sf.p].join(' ')); p._slug = slug(p.n); };
try { (JSON.parse(localStorage.getItem(STORE) || '[]') || []).map(cleanMine).filter(Boolean).forEach(p => { p.i = S.length; S.push(p); indexOne(p); house(p); }); } catch {}
const saveMine = () => { try { localStorage.setItem(STORE, JSON.stringify(S.filter(p => p.mine && !p.gone)
  .map(p => ({n:p.n, u:p.u, d:p.d, s:p.s, e:p.e, y:p.y, a:p.a, pc:p.pc, area:p.area, ll:p.ll, t:p.t, fp:p.fp})))); } catch {} };

/* ---------- state ---------- */
const active = new Set(Object.keys(STAGE));
let query = '', fSec = '', showSeat = false, selB = null, openI = null, hovB = null;
const visible = p => !p.gone && (active.has(p.st) || (p.st === 'unknown' && active.size === 3)) && (showSeat || p.k === 'office') && (!fSec || p.s === fSec) && (!query || p._s.includes(query));
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

const COLOR = k => ['match',['coalesce',['feature-state','st'],['get','st']],'scale',STAGE.scale[k],'growth',STAGE.growth[k],'early',STAGE.early[k],UNKNOWN[k]];
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
const offices = S.filter(p => p.k === 'office' && !p.mine);
$('stages').innerHTML = Object.entries(STAGE).map(([k, C]) =>
  `<button class="pill" type="button" data-k="${k}" aria-pressed="true" style="--c:var(${C.c})" title="${C.label}: ${C.hint}"><i></i>${C.short} <em></em></button>`).join('');
$('stages').querySelectorAll('.pill').forEach(b => b.onclick = () => {
  const k = b.dataset.k, only = active.size === 1 && active.has(k);
  if (only) Object.keys(STAGE).forEach(x => active.add(x)); else { active.clear(); active.add(k); }
  $('stages').querySelectorAll('.pill').forEach(x => x.setAttribute('aria-pressed', active.has(x.dataset.k)));
  shown = 40; apply();
});
const secs = [...new Set(S.map(p => p.s))].sort((a, b) => offices.filter(p => p.s === b).length - offices.filter(p => p.s === a).length);
secs.forEach(v => { const o = document.createElement('option'); o.value = v; $('fSec').appendChild(o); });
/* stage and sector counts (offices only); rerun when a startup is added or removed */
function counts(){
  const off = S.filter(p => p.k === 'office' && !p.gone);
  $('stages').querySelectorAll('.pill').forEach(b => { b.querySelector('em').textContent = nf.format(off.filter(p => p.st === b.dataset.k).length); });
  [...$('fSec').options].slice(1).forEach(o => { o.textContent = `${o.value} (${nf.format(off.filter(p => p.s === o.value).length)})`; });
}
counts();
$('fSec').onchange = e => { fSec = e.target.value; shown = 40; apply(); if (fSec) fitVisible(); };
$('seat').onchange = e => { showSeat = e.target.checked; apply(); };
let qt; $('q').addEventListener('input', e => { clearTimeout(qt); qt = setTimeout(() => { query = fold(e.target.value.trim()); listOpen = !!query || listOpen; shown = 40; apply(); }, 120); });
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') { const r = $('results').querySelector('.res'); r && r.click(); } });
let listOpen = false, shown = 40;
$('browse').onclick = () => { listOpen = !listOpen; renderPanel(); };
$('legend').innerHTML = [...Object.values(STAGE), UNKNOWN].map(C => `<span><i style="--c:var(${C.c})"></i>${C.label}</span>`).join('') + `<span>Colour = stage of the largest startup in the building</span>`;

function renderPanel(){
  const vis = S.filter(visible);
  const nb = visB.length;
  $('total').textContent = `${nf.format(vis.length)} startups · ${nf.format(nb)} buildings`;
  $('browse').textContent = listOpen ? 'Hide the list' : 'Browse the list';
  const R = $('results');
  if (!listOpen) { R.hidden = true; return; }
  R.hidden = false;
  vis.sort(bySize);
  R.innerHTML = (vis.length ? vis.slice(0, shown).map(p => `<button class="res${p.i === openI ? ' on' : ''}" type="button" data-i="${p.i}" style="--c:var(${ST(p.st).c})">
      ${logo(p, 'sm')}<div><b>${esc(p.n)}</b><span>${p.mine ? '<em class="mine-tag">Yours</em> ' : ''}${esc([p.s, p.cp || p.area].filter(Boolean).join(' · '))}</span></div><small>${p.e ? nf.format(p.e) : ''}</small></button>`).join('')
    : `<div class="none">No startup matches. Try another word or clear a filter.</div>`) +
    (vis.length > shown ? `<button class="more" type="button">Show ${nf.format(Math.min(200, vis.length - shown))} more</button>` : '');
  wireLogos(R);
  R.querySelectorAll('.res').forEach(b => b.onclick = () => { pick(+b.dataset.i); if (mobile()) { listOpen = false; renderPanel(); } });
  const m = R.querySelector('.more'); if (m) m.onclick = () => { shown += 200; renderPanel(); };
}

/* ---------- card: one building, the startups inside ---------- */
const card = $('card');
card.addEventListener('click', e => { const b = e.target.closest('[data-rm]'); if (b) removeMine(S[+b.dataset.rm]); });
const SRC = {manual:'a manual check', research:'job posts and press', website:'its own website', linkedin:'its LinkedIn page', cohort:'a Station F programme list', register:'the company register'};
const FIX = 'https://github.com/ethan-ab/paris-startup/issues/new';
function detail(p, hero){
  const C = ST(p.st);
  const host = (() => { try { return new URL(p.u).hostname.replace(/^www\./, ''); } catch { return ''; } })();
  const team = p.e ? nf.format(p.e) + (p.eh === 'register' ? ' (band)' : '') : '';
  const facts = [['Founded', p.y], ['Team', team], ['Raised', money(p.f)], ['Last round', p.rd, p.rs]].filter(r => r[1]);
  const where = [p.a, p.pc && (/^75/.test(p.pc) ? `${p.pc} Paris` : p.pc)].filter(Boolean).join(', ');
  const ev = (p.ev || [p.v]).map(k => SRC[k] || k);
  const conf = p.cf === 'v' ? ['ok', 'Verified office', `Source: ${ev.join(' + ')}`]
    : p.cf === 'l' ? ['mid', 'Likely office', `Only source: ${ev[0]}`]
    : ['warn', 'Unconfirmed address', `From ${ev[0]} only. The team may work elsewhere.`];
  if (p.mine) return `${hero ? '' : `<div class="meta"><span class="tag" style="--ci:var(${C.ci});--cs:var(${C.cs})">${C.label}</span></div>`}
    ${p.d ? `<p class="desc">${esc(p.d)}</p>` : ''}
    ${facts.length ? `<div class="facts">${facts.map(([k, v]) => `<div><span>${k}</span><b>${esc(v)}</b></div>`).join('')}</div>` : ''}
    <div class="where mid"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>
      <div><b>${esc(where || 'Pinned on the map')}</b><span>Office added by you</span></div></div>
    <div class="yours"><b>Added by you</b>Only on your map for now (saved in this browser). Send it for review to add it to the atlas for everyone.
      <div class="acts"><a class="send" href="${esc(reviewHref(p))}" target="_blank" rel="noopener">Send for review ↗</a><button class="rm" type="button" data-rm="${p.i}">Remove</button></div></div>
    <div class="links">${/^https?:\/\//i.test(p.u || '') ? `<a class="btn pri" href="${esc(p.u)}" target="_blank" rel="noopener">${esc(host)} ↗</a>` : ''}
      <a class="btn" href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(p.n + ', ' + where)}" target="_blank" rel="noopener">Directions</a></div>`;
  const fix = `${FIX}?title=${encodeURIComponent('Office: ' + p.n)}&body=${encodeURIComponent(`${p.n} (${p.u || ''})\nMap says: ${where}\nActual office: `)}`;
  return `${hero ? '' : `<div class="meta"><span class="tag" style="--ci:var(${C.ci});--cs:var(${C.cs})">${C.label}</span></div>`}
    ${p.d ? `<p class="desc">${esc(p.d)}${p.d.length >= 259 ? '…' : ''}</p>` : ''}
    ${facts.length ? `<div class="facts">${facts.map(([k, v, href]) => `<div><span>${k}</span><b>${/^https?:\/\//i.test(href || '') ? `<a href="${esc(href)}" target="_blank" rel="noopener" title="Source">${esc(v)} ↗</a>` : esc(v)}</b></div>`).join('')}</div>` : ''}
    <div class="where ${conf[0]}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>
      <div><b>${esc(where || 'Address unknown')}</b><span>${conf[1]} · ${esc(conf[2])}</span></div></div>
    ${p.sf ? `<div class="sfline ${p.sf.s}">${p.sf.s === 'here' ? `At Station F · ${esc(p.sf.p)}${p.sf.y ? ' (' + esc(p.sf.y) + ')' : ''}`
      : p.sf.s === 'moved' ? `Moved out of Station F · was in ${esc(p.sf.p)}${p.sf.y ? ' (' + esc(p.sf.y) + ')' : ''}`
      : `Was at Station F (${esc(p.sf.p)}${p.sf.y ? ', ' + esc(p.sf.y) : ''}) · current office not confirmed`}</div>` : ''}
    <div class="links">${/^https?:\/\//i.test(p.u || '') ? `<a class="btn pri" href="${esc(p.u)}" target="_blank" rel="noopener">${esc(host)} ↗</a>` : ''}
      <a class="btn" href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(p.n + ', ' + where)}" target="_blank" rel="noopener">Directions</a>
      <a class="fix" href="${esc(fix)}" target="_blank" rel="noopener">Wrong office?</a></div>`;
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
  const counts = [...Object.keys(STAGE), 'unknown'].map(k => [k, list.filter(p => p.st === k).length]).filter(x => x[1]);
  const LC = ST(lead.st);
  card.innerHTML = list.length === 1
    ? `<div class="ch hero"><div class="eyebrow">${esc(place)}</div>
        <div class="hrow">${logo(lead, 'lg')}<div class="ht"><h2>${esc(lead.n)}</h2>
          <div class="sub"><span class="tag" style="--ci:var(${LC.ci});--cs:var(${LC.cs})">${LC.label}</span><span>${esc(lead.s)}</span></div></div></div>
        <button class="x" type="button" aria-label="Close">×</button></div>
      <div class="det solo">${detail(lead, true)}</div>`
    : `<div class="ch"><div class="eyebrow">${esc(place)}</div>
        <h2>${nf.format(list.length)} startups in this building</h2>
        <div class="sbar">${counts.map(([k, n]) => `<i style="--c:var(${ST(k).c});flex-grow:${n}" title="${ST(k).label}: ${n}"></i>`).join('')}</div>
        <button class="x" type="button" aria-label="Close">×</button></div>
      ${lead.cp === 'Station F' && LEFT_SF ? `<div class="co"><button type="button" class="alumni"><span class="lg lg-sm u" aria-hidden="true"><b>→</b></span><div><b>${nf.format(LEFT_SF)} startups have left Station F</b><span>Show where they moved</span></div><small></small><em class="chev" aria-hidden="true"></em></button></div>` : ''}
      ${list.map(p => `<div class="co"><button type="button" data-i="${p.i}" aria-expanded="${p.i === i}">
          ${logo(p, 'sm')}<div><b>${esc(p.n)}</b><span>${esc(p.s)}</span></div><small>${p.e ? nf.format(p.e) : ''}</small><em class="chev" aria-hidden="true"></em></button>
          <div class="det" ${p.i === i ? '' : 'hidden'}>${p.i === i ? detail(p) : ''}</div></div>`).join('')}`;
  wireLogos(card);
  card.hidden = false;
  card.querySelector('.x').onclick = closeCard;
  const alumni = card.querySelector('.alumni');
  if (alumni) alumni.onclick = () => { $('q').value = 'left station f'; query = 'left station f'; listOpen = true; shown = 40; closeCard(); apply(); fitVisible(); };
  card.querySelectorAll('.co>button:not(.alumni)').forEach(btn => btn.onclick = () => {
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
map.on('click', e => { if (picking) { pinMk.setLngLat(e.lngLat); return; } const b = bAt(e.point, coarse ? 10 : 3); if (b != null) { showCard(b, null, false); renderPanel(); } else if (selB !== null) closeCard(); });
map.on('mousemove', e => {
  const b = coarse || picking ? null : bAt(e.point, 2);
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
addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (picking) return cancelPick();
  if (!modal.hidden) return hitsEl.hidden ? closeForm() : hideHits();
  closeCard();
});

/* ---------- add a startup: form → lights up its building on the map right away ---------- */
const modal = $('modal'), form = $('adForm'), locEl = $('adLoc'), hitsEl = $('adHits');
const GEO = 'https://data.geopf.fr/geocodage'; // French national address base (BAN), CORS-enabled
$('adSec').insertAdjacentHTML('beforeend', [...new Set(S.filter(p => !p.mine).map(p => p.s))].sort((a, b) => a.localeCompare(b)).map(v => `<option>${esc(v)}</option>`).join(''));
let draft = null, lastFocus = null;   // draft: {ll:[lat,lng], a, pc, area}
const toSpot = f => {
  const [lng, lat] = f.geometry.coordinates, pr = f.properties || {}, pc = String(pr.postcode || '');
  if (!inZone(lat, lng)) return null;
  const n = /^75(\d{3})$/.exec(pc), arr = n ? (+n[1] === 116 ? 16 : +n[1]) : 0;
  return {ll:[+lat.toFixed(6), +lng.toFixed(6)], a:pr.name || '', pc, area:AREA[pc] || (arr ? `${arr}${arr === 1 ? 'er' : 'e'}` : pr.city || '')};
};
async function geo(path){
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 5000);
  try { const r = await fetch(GEO + path, {signal:ctl.signal}); if (!r.ok) throw new Error(r.status); return ((await r.json()).features || []).map(toSpot).filter(Boolean); }
  finally { clearTimeout(t); }
}
const geocode = q => geo(`/search?q=${encodeURIComponent(q)}&limit=8&lat=48.8566&lon=2.3522`);
const reverse = ll => geo(`/reverse?lat=${ll[0]}&lon=${ll[1]}&limit=1`);
const nearestArea = ll => { let best = null, bd = 1e9; S.forEach(p => { if (p.mine || !p.area) return; const d = (p.ll[0]-ll[0])**2 + ((p.ll[1]-ll[1]) * .66)**2; if (d < bd) { bd = d; best = p; } }); return best ? {pc:best.pc, area:best.area} : {pc:'', area:''}; };

let toastT = 0;
function toast(t){ const el = $('toast'); el.textContent = t; el.hidden = false; clearTimeout(toastT); toastT = setTimeout(() => { el.hidden = true; }, 3500); }
function setSpot(){
  $('adSpot').hidden = !draft;
  $('adSpot').textContent = draft ? `Pinned: ${[draft.a, draft.area].filter(Boolean).join(' · ') || 'on the map'}` : '';
  $('adPick').textContent = draft ? 'Move pin' : 'Pick on map';
}
function stageHint(){
  const e = parseInt($('adTeam').value, 10), st = e > 0 ? stageOf(e) : null;
  $('adStage').innerHTML = st ? `<i style="--c:var(${STAGE[st].c})"></i>Shows as ${STAGE[st].label.toLowerCase()} (${STAGE[st].hint})` : '';
}
$('adTeam').addEventListener('input', stageHint);
function openForm(){
  if (picking) endPick();
  lastFocus = document.activeElement;
  form.reset(); draft = null; setSpot(); hideHits(); stageHint();
  $('adMsg').className = 'msg'; $('adMsg').textContent = ''; $('adGo').disabled = false;
  modal.hidden = false; $('adName').focus();
}
function closeForm(){ modal.hidden = true; hideHits(); lastFocus && lastFocus.focus && lastFocus.focus({preventScroll:true}); }
$('addBtn').onclick = openForm;
$('adClose').onclick = closeForm;
modal.addEventListener('click', e => { if (e.target === modal) closeForm(); });

/* address autocomplete */
let hits = [], hitOn = -1, geoTimer = 0, geoSeq = 0;
function hideHits(){ hits = []; hitOn = -1; hitsEl.hidden = true; hitsEl.textContent = ''; locEl.setAttribute('aria-expanded', 'false'); locEl.removeAttribute('aria-activedescendant'); }
function paintHits(){
  hitsEl.innerHTML = hits.map((h, j) => `<li role="option" id="adHit${j}" aria-selected="${j === hitOn}">${esc(h.a)}<small>${esc([h.pc, h.area].filter(Boolean).join(' · '))}</small></li>`).join('');
  hitsEl.querySelectorAll('li').forEach((li, j) => li.onmousedown = e => { e.preventDefault(); chooseHit(hits[j]); });
  hitsEl.hidden = !hits.length; locEl.setAttribute('aria-expanded', String(!!hits.length));
  if (hitOn >= 0) locEl.setAttribute('aria-activedescendant', 'adHit' + hitOn); else locEl.removeAttribute('aria-activedescendant');
}
function chooseHit(h){ draft = h; locEl.value = h.a; hideHits(); setSpot(); }
function searchAddress(){
  clearTimeout(geoTimer);
  const q = locEl.value.trim(), seq = ++geoSeq;
  if (q.length < 3) return hideHits();
  geoTimer = setTimeout(async () => {
    try { const r = await geocode(q); if (seq === geoSeq && document.activeElement === locEl) { hits = r.slice(0, 5); hitOn = -1; paintHits(); } }
    catch { if (seq === geoSeq) hideHits(); }
  }, 250);
}
locEl.addEventListener('input', () => { draft = null; setSpot(); searchAddress(); });
locEl.addEventListener('focus', () => { if (!draft) searchAddress(); });
locEl.addEventListener('keydown', e => {
  if (hitsEl.hidden || !hits.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); hitOn = (hitOn + (e.key === 'ArrowDown' ? 1 : hits.length - 1)) % hits.length; paintHits(); }
  else if (e.key === 'Enter') { e.preventDefault(); chooseHit(hits[Math.max(0, hitOn)]); }
});
locEl.addEventListener('blur', () => setTimeout(hideHits, 120));

/* pick the office on the map with a draggable pin */
let picking = false, pinMk = null;
const pinSvg = col => `<svg class="newpin" viewBox="0 0 30 40" width="30" height="40" aria-hidden="true"><path d="M15 39S2.5 24.5 2.5 14.5a12.5 12.5 0 0 1 25 0C27.5 24.5 15 39 15 39z" fill="${col}" stroke="#fff" stroke-width="2.5"/><circle cx="15" cy="14.5" r="4.5" fill="#fff"/></svg>`;
function startPick(){
  hideHits(); modal.hidden = true; picking = true; closeCard(); tip.hidden = true;
  $('pkName').textContent = $('adName').value.trim() || 'Your startup';
  $('picker').hidden = false; $('addBtn').hidden = true; document.body.classList.add('picking');
  if (!pinMk) pinMk = new maplibregl.Marker({element:document.createElement('div'), anchor:'bottom', draggable:true});
  pinMk.getElement().innerHTML = pinSvg(ST(stageOf(parseInt($('adTeam').value, 10) || 0)).hex);
  const at = draft ? [draft.ll[1], draft.ll[0]] : map.getCenter().toArray();
  pinMk.setLngLat(at).addTo(map);
  map.easeTo({center:at, zoom:Math.max(map.getZoom(), 16.2), pitch:Math.min(map.getPitch(), 30), duration:600});
  $('pkOk').focus({preventScroll:true});
}
function endPick(){ picking = false; $('picker').hidden = true; $('addBtn').hidden = false; document.body.classList.remove('picking'); pinMk && pinMk.remove(); }
function cancelPick(){ endPick(); modal.hidden = false; $('adPick').focus(); }
async function confirmPick(){
  const {lng, lat} = pinMk.getLngLat();
  if (!inZone(lat, lng)) { toast('Move the pin to Paris or the inner suburbs'); return; }
  const ll = [+lat.toFixed(6), +lng.toFixed(6)], spot = draft = {ll, a:'', ...nearestArea(ll)};
  endPick(); modal.hidden = false; setSpot(); $('adPick').focus();
  try {   // fill in the street and postcode when the address service is reachable
    const [r] = await reverse(ll);
    if (r && draft === spot) { Object.assign(spot, {a:r.a, pc:r.pc, area:r.area}); if (!locEl.value.trim()) locEl.value = r.a; setSpot(); }
  } catch {}
}
$('adPick').onclick = startPick;
$('pkCancel').onclick = cancelPick;
$('pkOk').onclick = confirmPick;

form.addEventListener('submit', async e => {
  e.preventDefault();
  const f = new FormData(form), msg = $('adMsg'), go = $('adGo');
  const err = t => { msg.className = 'msg err'; msg.textContent = t; };
  if (f.get('website')) return; // honeypot
  const v = k => String(f.get(k) || '').trim();
  const name = v('name'), loc = v('address'), sec = v('sector'), url = v('url'), team = v('team'), year = v('year');
  if (name.length < 2) return err('Add the name of the startup.');
  if (!draft && !loc) return err('Add the office address, or pick it on the map.');
  if (!sec) return err('Choose a sector.');
  if (team && !(+team >= 1 && +team <= 100000 && Number.isInteger(+team))) return err('Team size should be a whole number.');
  if (year && !(+year >= 1990 && +year <= new Date().getFullYear() && Number.isInteger(+year))) return err('Founding year looks off.');
  if (url && !/^https?:\/\/[^\s.]+\.[^\s]+$/i.test(url)) return err('Website should start with https://');
  if (!draft) {
    go.disabled = true; msg.className = 'msg'; msg.textContent = 'Finding the address…';
    try { draft = (await geocode(loc))[0] || null; } catch { draft = null; }
    go.disabled = false; msg.textContent = '';
    if (!draft) return err("Couldn't find that address around Paris. Pick a suggestion, or place it on the map.");
  }
  const p = cleanMine({n:name, u:url, d:v('desc'), s:sec, e:team ? +team : undefined, y:year ? +year : undefined,
    a:draft.a || loc, pc:draft.pc, area:draft.area, ll:draft.ll, t:Date.now()});
  if (!p) return err('Add the name of the startup.');
  go.disabled = true;
  await addStartup(p);
});

/* the real footprint of the building under the office, read from the basemap's own 3D buildings */
function footprint(ll){
  if (!ready || !map.getLayer('building-3d')) return null;
  const pt = map.project([ll[1], ll[0]]);
  for (const f of map.queryRenderedFeatures([[pt.x - 3, pt.y - 3], [pt.x + 3, pt.y + 3]], {layers:['building-3d']})) {
    const pg = polys(f.geometry).find(pg => inRing(ll[1], ll[0], pg[0]));
    if (pg && pg[0].length <= 400) {
      const pr = f.properties || {};
      return {ring:pg[0].map(c => [+c[0].toFixed(6), +c[1].toFixed(6)]), h:Math.min(Math.max(+(pr.render_height ?? pr.h) || 15, 3), 300)};
    }
  }
  return null;
}
const settle = () => new Promise(res => { if (!ready) return res(); const t = setTimeout(res, 3500); map.once('idle', () => { clearTimeout(t); res(); }); });
async function addStartup(p){
  modal.hidden = true; hideHits(); closeCard();
  // make sure nothing hides it
  Object.keys(STAGE).forEach(k => active.add(k)); $('stages').querySelectorAll('.pill').forEach(x => x.setAttribute('aria-pressed', 'true'));
  query = ''; $('q').value = ''; fSec = ''; $('fSec').value = '';
  // look straight down on the spot so the building under it can be read off the basemap
  if (ready && !B.some(f => !f.gone && contains(f.geometry, p.ll))) {
    map.jumpTo({center:[p.ll[1], p.ll[0]], zoom:17.2, pitch:0, bearing:0});
    await settle();
    p.fp = footprint(p.ll) || undefined;
  }
  p.i = S.length; S.push(p); indexOne(p); house(p); saveMine();
  if (ready) map.getSource('sb').setData(BUILDINGS);
  counts(); apply(); pick(p.i);
  $('adGo').disabled = false;
  toast(`${p.n} is on the map`);
}
function removeMine(p){
  if (!p || !p.mine || p.gone) return;
  const f = byB.get(p.b);
  p.gone = true; saveMine(); closeCard();
  if (f) {
    f.properties.s = f.properties.s.filter(k => k !== p.i);
    if (f.mine && !f.properties.s.length) { f.gone = true; B.splice(B.indexOf(f), 1); byB.delete(f.properties.b); }
    if (ready) map.getSource('sb').setData(BUILDINGS);
  }
  counts(); apply();
  toast(`Removed ${p.n}`);
}
function reviewHref(p){
  const where = [p.a, p.pc, p.area].filter(Boolean).join(', ');
  const body = [`**Startup**: ${p.n}`, p.u && `**Website**: ${p.u}`, `**Office**: ${where} (${p.ll[0]}, ${p.ll[1]})`, `**Sector**: ${p.s}`,
    p.e && `**Team size**: ${p.e}`, p.y && `**Founded**: ${p.y}`, p.d && `**What it does**: ${p.d}`].filter(Boolean).join('\n\n');
  return `${FIX}?labels=new-startup&title=${encodeURIComponent('Add: ' + p.n)}&body=${encodeURIComponent(body)}`;
}

apply();

/* Read-only hooks for end-to-end tests. */
window.__psa = {map, startups:S, buildings:B, isReady:() => ready, visibleBuildings:() => visB.length, selected:() => selB, picking:() => picking};
})();
