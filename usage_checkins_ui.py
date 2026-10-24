"""
usage_checkins_ui.py - check-in tab (HTML + CSS + JS) shared by both dashboards.

The page gets all data as one JSON blob (see `page_data`), so the same script works over
http:// (with the buttons) and file:// (read-only). Writes are POSTs that carry the
X-Usage-Token header read from <meta name="usage-token">.
"""

import html
import json
from datetime import datetime, timedelta

import usage_checkins

# Strings the tab needs; callers pass their own language table with these keys.
KEYS = ("name", "note", "category", "all", "none_ck", "range", "start_btn", "end_btn", "point_btn", "end_title",
        "session", "done", "interrupted", "running", "open", "point", "status", "duration", "usage_d", "unavailable",
        "reason", "save", "cancel", "no_open", "search", "from", "to", "matches", "total", "total_time",
        "count", "interrupted_n", "open_n", "overlap", "per_cat", "per_name", "by_day", "day_sub", "max_cats",
        "loading", "conn_err", "time", "hint", "need_name", "pick_cat", "pt")

EN = {
    "name": "Name", "note": "Note", "category": "Categories", "all": "All", "none_ck": "No check-ins",
    "range": "Range", "start_btn": "Start", "end_btn": "End…", "point_btn": "Check in", "end_title": "End a session",
    "session": "Session", "done": "Done", "interrupted": "Interrupted", "running": "In progress",
    "open": "Open (not ended)", "point": "Check-in", "status": "Status", "duration": "Duration",
    "usage_d": "Usage Δ", "unavailable": "unavailable", "reason": "Note / reason (optional)", "save": "Save",
    "cancel": "Cancel", "no_open": "No open session", "search": "Search name, note, category…", "from": "From",
    "to": "To", "matches": "{n} match(es)", "total": "Total", "total_time": "Total time", "count": "Sessions",
    "interrupted_n": "Interrupted", "open_n": "Open",
    "overlap": "A session with several categories counts fully under each one, so category totals can overlap; "
               "the Total row counts each session once.",
    "per_cat": "Per category", "per_name": "Per name", "by_day": "By day",
    "day_sub": "{n} session(s) · {t} · usage {u}", "max_cats": "Pick at most {n} categories",
    "loading": "Saving…", "conn_err": "Cannot reach local server", "time": "Time", "hint": "",
    "need_name": "Enter a name", "pick_cat": "Pick 1–{n}", "pt": "%",
}
ZH_TW = {
    "name": "名字", "note": "備註", "category": "類別", "all": "全部", "none_ck": "尚無打卡",
    "range": "範圍", "start_btn": "開始", "end_btn": "結束…", "point_btn": "打卡", "end_title": "結束一段工作",
    "session": "工作段", "done": "完成", "interrupted": "中斷", "running": "進行中", "open": "未結束",
    "point": "單次打卡", "status": "狀態", "duration": "時長", "usage_d": "用量變化", "unavailable": "無資料",
    "reason": "備註／原因（選填）", "save": "儲存", "cancel": "取消", "no_open": "沒有未結束的工作段",
    "search": "搜尋名字、備註、類別…", "from": "從", "to": "到", "matches": "{n} 筆符合", "total": "合計",
    "total_time": "總時間", "count": "段數", "interrupted_n": "中斷", "open_n": "未結束",
    "overlap": "一段工作若選了多個類別，會完整計入每個類別，所以各類別時間加總可能重疊；「合計」列每段只算一次。",
    "per_cat": "各類別統計", "per_name": "各名字統計", "by_day": "依日期",
    "day_sub": "{n} 段 · {t} · 用量 {u}", "max_cats": "最多選 {n} 個類別",
    "loading": "儲存中…", "conn_err": "無法連線到本機服務", "time": "時間", "hint": "",
    "need_name": "請輸入名字", "pick_cat": "選 1–{n} 個", "pt": "%",
}
NUMERIC = dict(EN, **{
    "name": "", "note": "", "category": "", "all": "∗", "none_ck": "∅", "range": "", "start_btn": "▶",
    "end_btn": "■", "point_btn": "✓", "end_title": "■", "session": "", "done": "✓", "interrupted": "⨯",
    "running": "…", "open": "?", "point": "·", "status": "", "duration": "⏱", "usage_d": "Δ", "unavailable": "∅",
    "reason": "", "save": "✓", "cancel": "⨯", "no_open": "∅", "search": "…", "from": "≥", "to": "≤",
    "matches": "{n}", "total": "∑", "total_time": "⏱", "count": "#", "interrupted_n": "⨯", "open_n": "?",
    "overlap": "", "per_cat": "", "per_name": "", "by_day": "", "day_sub": "{n} · {t} · Δ {u}", "max_cats": "≤ {n}",
    "loading": "…", "conn_err": "⨯", "time": "", "need_name": "?", "pick_cat": "1–{n}",
})


def page_data(sessions, alias, colors, cat_label, cat_ids, max_categories, tz, cuts, cut_labels, strings,
              samples, endpoints, privacy=False, zlabel=""):
    """JSON-ready dict for the tab. `sessions` from usage_checkins.sessions()+attach_usage();
    names are replaced by `alias` (privacy) and notes dropped when privacy is on."""
    rows, days = [], set()
    for s in sessions:
        st = s["start"].astimezone(tz)
        en = s["end"].astimezone(tz) if s["end"] else None
        u = s.get("usage") or {}
        days.add(st.strftime("%Y-%m-%d"))
        rows.append({"id": s["id"], "n": alias.get(s["name"], s["name"]), "c": colors.get(s["name"], "#4D8DFF"),
                     "k": s["cats"], "e": int(s["start"].timestamp()), "day": st.strftime("%Y-%m-%d"),
                     "t1": st.strftime("%H:%M"), "t2": en.strftime("%H:%M") if en else "",
                     "t2d": en.strftime("%Y-%m-%d") if en else "",
                     "st": s["state"], "dur": round(s["duration_s"] / 60.0, 1) if s.get("duration_s") is not None else None,
                     "u": u.get("delta") if u.get("status") == "ok" else None,
                     "note": "" if privacy else s.get("note", ""), "rn": "" if privacy else s.get("end_note", "")})
    rise = {}
    for d in days:
        d0 = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=tz)
        rise[d] = usage_checkins.day_usage_rise(samples, d0, d0 + timedelta(days=1))
    return {"rows": rows, "cats": [{"id": c, "label": cat_label(c)} for c in cat_ids], "max": int(max_categories),
            "cut": cuts, "labels": cut_labels, "L": {k: strings.get(k, EN.get(k, "")) for k in KEYS},
            "tz": zlabel, "rise": rise, "ep": endpoints, "priv": bool(privacy)}


def to_script_json(data) -> str:
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def pane_html(L, hint_html=""):
    e = lambda k: html.escape(L.get(k, EN.get(k, "")))  # noqa: E731
    return f"""<section id='p-checkin'>
<div class='ckform' id='ckform' hidden><input id='ckname' list='cknames' maxlength='40' placeholder='{e("name")}'><datalist id='cknames'></datalist>
<span class='chips' id='ckchips'></span><input id='cknote' maxlength='200' placeholder='{e("note")}'>
<button class='ckb' id='ckstart'>{e("start_btn")}</button><button class='ckb' id='ckend'>{e("end_btn")}</button><button class='ckb' id='ckpoint'>{e("point_btn")}</button><span id='ckmsg'></span></div>
<dialog id='ckdlg'><form method='dialog'><b>{e("end_title")}</b><p><select id='ckopen'></select></p>
<p><label><input type='radio' name='ckst' value='done' checked> {e("done")}</label> <label><input type='radio' name='ckst' value='interrupted'> {e("interrupted")}</label></p>
<p><input id='ckreason' maxlength='200' placeholder='{e("reason")}'></p><p><button class='ckb' id='ckok' value='ok'>{e("save")}</button> <button class='ckb' value='cancel'>{e("cancel")}</button></p></form></dialog>
<div class='sub' id='ckhint'>{hint_html}</div>
<div class='ckfilter'><input id='cksearch' type='search' placeholder='{e("search")}'> {e("name")} <select id='ckf'><option value=''>{e("all")}</option></select>
{e("from")} <input id='ckfrom' type='date'> {e("to")} <input id='ckto' type='date'> {e("range")} <span id='ckr'></span></div>
<div class='ckfilter'>{e("category")} <span class='chips' id='ckfc'></span> <b id='ckcount'></b></div>
<div class='tables'><table id='ckdays'><caption>{e("by_day")}</caption></table>
<div><table id='ckcats'><caption>{e("per_cat")}</caption></table><div class='sub'>{e("overlap")}</div>
<table id='cksum'><caption>{e("per_name")}</caption></table></div></div></section>"""


CSS = """.chips{display:inline-flex;gap:4px;flex-wrap:wrap}.chips label{border:1px solid var(--bd,#2a3a4a);border-radius:999px;padding:2px 9px;font-size:12px;cursor:pointer;user-select:none}
.chips input{display:none}.chips label.on{border-color:var(--under);color:var(--under);font-weight:700}
.ckb{background:transparent;border:1px solid var(--bd,#2a3a4a);color:inherit;font:inherit;font-weight:700;padding:5px 12px;border-radius:7px;cursor:pointer}
.ckb:disabled{opacity:.5;cursor:default} #ckdlg{background:var(--card,#0d1117);color:inherit;border:1px solid var(--bd,#2a3a4a);border-radius:10px}
tr.day td{font-weight:700;border-top:2px solid var(--bd,#2a3a4a)} .st-interrupted,.st-open{color:var(--notice);font-weight:700}
.st-running{color:var(--under)} .ckfilter{margin:6px 0} #ckmsg{color:var(--notice);font-weight:700}"""


JS = r"""(function(){var D=JSON.parse(document.getElementById('ckdata').textContent),R=D.rows,T=D.L,K=Object.keys(D.cut),cur=K[0];
var $=function(id){return document.getElementById(id);},served=/^https?:$/.test(location.protocol);
var TK=(document.querySelector('meta[name="usage-token"]')||{}).content||'';
function el(t,x,c){var e=document.createElement(t);if(x!=null)e.textContent=x;if(c)e.className=c;return e;}
function f(t,o){return String(t).replace(/\{(\w+)\}/g,function(_,k){return o[k]!=null?o[k]:'';});}
function dur(m){if(m==null)return '—';m=Math.round(m);return m>=60?Math.floor(m/60)+'h'+String(m%60).padStart(2,'0')+'m':m+'m';}
function pts(v){if(v==null)return T.unavailable;var r=Math.round(v*10)/10;return (r>0?'▲ +'+r:(r<0?'▼ '+r:'±0'))+(T.pt||'%');}
var CL={};D.cats.forEach(function(c){CL[c.id]=c.label;});
function chips(box,onchange,limit){D.cats.forEach(function(c){var l=el('label',c.label),i=el('input');i.type='checkbox';i.value=c.id;l.prepend(i);
 l.onclick=function(ev){ev.preventDefault();var on=box.querySelectorAll('input:checked').length;
  if(!i.checked&&limit&&on>=limit){$('ckmsg').textContent=f(T.max_cats,{n:limit});return;}
  i.checked=!i.checked;l.classList.toggle('on',i.checked);$('ckmsg').textContent='';if(onchange)onchange();};box.appendChild(l);});}
function picked(box){return [].map.call(box.querySelectorAll('input:checked'),function(i){return i.value;});}
chips($('ckchips'),null,D.max);chips($('ckfc'),draw,0);
var names=[];R.forEach(function(r){if(names.indexOf(r.n)<0)names.push(r.n);});
names.forEach(function(n){$('ckf').appendChild(el('option',n)).value=n;$('cknames').appendChild(el('option')).value=n;});
K.forEach(function(k){var b=el('button',D.labels[k],'ckb');b.dataset.r=k;b.onclick=function(){cur=k;$('ckfrom').value='';$('ckto').value='';draw();};$('ckr').appendChild(b);});
function filtered(){var q=$('cksearch').value.toLowerCase().split(/\s+/).filter(Boolean),nm=$('ckf').value,fc=picked($('ckfc')),
 d1=$('ckfrom').value,d2=$('ckto').value;
 return R.filter(function(r){if(d1||d2){if(d1&&r.day<d1)return false;if(d2&&r.day>d2)return false;}else if(r.e<D.cut[cur])return false;
  if(nm&&r.n!=nm)return false;if(fc.length&&!r.k.some(function(k){return fc.indexOf(k)>=0;}))return false;
  if(q.length){var h=[r.n,r.note,r.rn].concat(r.k.map(function(k){return CL[k]||k;})).join('\n').toLowerCase();
   if(!q.every(function(w){return h.indexOf(w)>=0;}))return false;}return true;});}
function stl(s){return T[s]||s;}
function ended(r){return r.st=='done'||r.st=='interrupted';}
function draw(){[].forEach.call($('ckr').children,function(b){b.classList.toggle('on',!$('ckfrom').value&&!$('ckto').value&&b.dataset.r==cur);});
 var rows=filtered(),Dt=$('ckdays'),C=$('ckcats'),S=$('cksum');[Dt,C,S].forEach(function(t){t.querySelectorAll('tr').forEach(function(x){x.remove();});});
 $('ckcount').textContent=f(T.matches,{n:rows.length});
 var hd=el('tr');[T.time+' ('+D.tz+')',T.name,T.category,T.note,T.duration,T.status,T.usage_d].forEach(function(x){hd.appendChild(el('th',x));});Dt.appendChild(hd);
 if(!rows.length){var tr=el('tr'),td=el('td',T.none_ck);td.colSpan=7;tr.appendChild(td);Dt.appendChild(tr);}
 var days={};rows.forEach(function(r){(days[r.day]=days[r.day]||[]).push(r);});
 Object.keys(days).sort().reverse().forEach(function(d){var xs=days[d].slice().sort(function(a,b){return a.e-b.e;}),
  tot=xs.reduce(function(a,r){return a+(ended(r)?(r.dur||0):0);},0),tr=el('tr',null,'day'),td=el('td',d+' — '+f(T.day_sub,{n:xs.length,t:dur(tot),u:pts(D.rise[d])}));
  td.colSpan=7;tr.appendChild(td);Dt.appendChild(tr);
  xs.forEach(function(r){var tr=el('tr'),a=el('td'),dot=el('span','●');dot.style.color=r.c;
   tr.appendChild(el('td',r.t1+(r.t2?'–'+r.t2+(r.t2d&&r.t2d!=r.day?' (+'+r.t2d.slice(5)+')':''):'')));
   a.appendChild(dot);a.appendChild(document.createTextNode(' '+r.n));tr.appendChild(a);
   tr.appendChild(el('td',r.k.map(function(k){return CL[k]||k;}).join(', ')));
   tr.appendChild(el('td',[r.note,r.rn].filter(Boolean).join(' / ')||'—'));
   tr.appendChild(el('td',r.st=='point'?'—':dur(r.dur)));tr.appendChild(el('td',stl(r.st),'st-'+r.st));
   tr.appendChild(el('td',ended(r)?pts(r.u):'—'));Dt.appendChild(tr);});});
 var h3=el('tr');[T.category,T.count,T.total_time,T.interrupted_n,T.open_n].forEach(function(x){h3.appendChild(el('th',x));});C.appendChild(h3);
 var by={};rows.forEach(function(r){r.k.forEach(function(k){var b=by[k]=by[k]||{n:0,t:0,i:0,o:0};b.n++;if(ended(r))b.t+=r.dur||0;if(r.st=='interrupted')b.i++;if(r.st=='open')b.o++;});});
 D.cats.forEach(function(c){var b=by[c.id];if(!b)return;var tr=el('tr');[c.label,b.n,dur(b.t),b.i,b.o].forEach(function(x){tr.appendChild(el('td',String(x)));});C.appendChild(tr);});
 var tt=el('tr',null,'day');[T.total,rows.length,dur(rows.reduce(function(a,r){return a+(ended(r)?(r.dur||0):0);},0)),
  rows.filter(function(r){return r.st=='interrupted';}).length,rows.filter(function(r){return r.st=='open';}).length].forEach(function(x){tt.appendChild(el('td',String(x)));});C.appendChild(tt);
 var h2=el('tr');[T.name,T.count,T.total_time].forEach(function(x){h2.appendChild(el('th',x));});S.appendChild(h2);
 var pn={};rows.forEach(function(r){var b=pn[r.n]=pn[r.n]||{n:0,t:0};b.n++;if(ended(r))b.t+=r.dur||0;});
 Object.keys(pn).forEach(function(n){var tr=el('tr');[n,pn[n].n,dur(pn[n].t)].forEach(function(x){tr.appendChild(el('td',String(x)));});S.appendChild(tr);});}
['cksearch','ckf','ckfrom','ckto'].forEach(function(id){$(id).oninput=draw;$(id).onchange=draw;});draw();
try{var ln=localStorage.getItem('checkin.lastName');if(ln)$('ckname').value=ln;}catch(e){}
if(served)fetch(D.ep.status).then(function(r){return r.ok?r.json():null;}).then(function(j){if(j&&(j.ok||j.server)){$('ckform').hidden=false;$('ckhint').hidden=true;}}).catch(function(){});
function post(body){var bs=document.querySelectorAll('.ckb');[].forEach.call(bs,function(b){b.disabled=true;});$('ckmsg').textContent=T.loading;
 fetch(D.ep.write,{method:'POST',headers:{'Content-Type':'application/json','X-Usage-Token':TK},body:JSON.stringify(body)})
 .then(function(r){return r.json();}).then(function(j){if(j.ok){location.hash='checkin';location.reload();return;}
  $('ckmsg').textContent=j.error||'?';[].forEach.call(bs,function(b){b.disabled=false;});})
 .catch(function(){$('ckmsg').textContent=T.conn_err;[].forEach.call(bs,function(b){b.disabled=false;});});}
function base(){var n=$('ckname').value.trim();if(!n){$('ckmsg').textContent=T.need_name;return null;}
 var c=picked($('ckchips'));if(c.length>D.max){$('ckmsg').textContent=f(T.max_cats,{n:D.max});return null;}
 try{localStorage.setItem('checkin.lastName',n);}catch(e){}return {name:n,categories:c.length?c:['other'],note:$('cknote').value};}
$('ckstart').onclick=function(){var b=base();if(b){b.phase='start';post(b);}};
$('ckpoint').onclick=function(){var b=base();if(b){b.phase='point';post(b);}};
$('ckend').onclick=function(){var n=$('ckname').value.trim(),sel=$('ckopen');sel.textContent='';
 R.filter(function(r){return (r.st=='running'||r.st=='open')&&(D.priv||!n||r.n==n);}).reverse().forEach(function(r){
  sel.appendChild(el('option',r.n+' · '+r.day+' '+r.t1+' · '+r.k.map(function(k){return CL[k]||k;}).join(', '))).value=r.id;});
 if(!sel.options.length){$('ckmsg').textContent=T.no_open;return;}$('ckreason').value='';
 if($('ckdlg').showModal)$('ckdlg').showModal();else $('ckdlg').setAttribute('open','');};
$('ckok').onclick=function(ev){ev.preventDefault();var st=document.querySelector('input[name=ckst]:checked').value;
 if($('ckdlg').close)$('ckdlg').close();post({phase:'end',id:$('ckopen').value,status:st,note:$('ckreason').value});};})();"""
