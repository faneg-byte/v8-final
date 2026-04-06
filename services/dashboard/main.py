import os
from flask import Flask,render_template_string,request
import psycopg2
from psycopg2.extras import RealDictCursor

app=Flask(__name__)

def gc():
    return psycopg2.connect(host=os.environ.get("DB_HOST","127.0.0.1"),port=int(os.environ.get("DB_PORT",5432)),
        dbname=os.environ.get("DB_NAME","v8engine"),user=os.environ.get("DB_USER","v8operator"),
        password=os.environ.get("DB_PASSWORD",""))

T=r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>V8 Engine</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,system-ui,sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh}
.layout{display:flex;min-height:100vh}
.sidebar{width:220px;background:#161b22;border-right:1px solid #30363d;padding:20px 16px;flex-shrink:0}
.sidebar h3{font-size:13px;color:#8b949e;font-weight:400;margin-bottom:12px}
a.sb{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:6px;cursor:pointer;font-size:13px;margin-bottom:4px;color:#e6edf3;text-decoration:none}
a.sb:hover{background:#21262d}a.sb.on{background:#21262d;color:#f85149}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.mn{flex:1;padding:24px;max-width:1000px;overflow-x:auto}
.hd{display:flex;align-items:center;gap:12px;margin-bottom:24px}
.hd h1{font-size:20px;font-weight:600}
.hd .st{margin-left:auto;display:flex;align-items:center;gap:6px;font-size:13px;color:#3fb950}
.sts{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px}
.st2{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}
.sl{font-size:11px;color:#8b949e;margin-bottom:2px}.sv{font-size:20px;font-weight:600}
.tabs{display:flex;border-bottom:1px solid #30363d;margin-bottom:12px}
.tab{padding:8px 16px;font-size:13px;color:#8b949e;cursor:pointer;border-bottom:2px solid transparent}
.tab.on{color:#f85149;border-bottom-color:#f85149}.tab:hover{color:#e6edf3}
.pn{display:none}.pn.on{display:block}
.fi{display:flex;gap:8px;margin-bottom:12px}
.fi input{background:#0d1117;border:1px solid #30363d;color:#e6edf3;padding:6px 10px;border-radius:6px;font-size:12px;width:200px}
.fi select{background:#0d1117;border:1px solid #30363d;color:#e6edf3;padding:6px 10px;border-radius:6px;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 6px;color:#8b949e;font-weight:400;border-bottom:1px solid #30363d;cursor:pointer;user-select:none;white-space:nowrap}
th:hover{color:#f85149}td{padding:8px 6px;border-bottom:1px solid #21262d}
.p{font-size:11px;padding:2px 10px;border-radius:12px;display:inline-block}
.pg{background:#0d3321;color:#3fb950}.pr{background:#3d1114;color:#f85149}
.pb{background:#0c2d6b;color:#58a6ff}.py{background:#3d2e00;color:#d29922}.pp{background:#271052;color:#bc8cff}
.m{font-family:monospace}
.pc{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin-bottom:10px}
.ph{display:flex;align-items:center;gap:8px;margin-bottom:10px}
.sg{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.sc{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}
.gb{height:4px;border-radius:2px;background:#21262d;margin-top:3px}
.gf{height:100%;border-radius:2px}
.gr{display:flex;justify-content:space-between;font-size:11px;color:#8b949e;margin-top:5px}
.ab{height:4px;border-radius:2px;background:#21262d;margin-top:4px}
.af{height:100%;border-radius:2px}
@media(max-width:768px){.layout{flex-direction:column}.sidebar{width:100%;border-right:none;border-bottom:1px solid #30363d;padding:12px;display:flex;flex-wrap:wrap;gap:4px}a.sb{width:auto;padding:6px 12px}.sts{grid-template-columns:repeat(2,1fr)}.sg{grid-template-columns:1fr}}
</style></head><body>
<div class="layout">
<div class="sidebar">
<h3>Sectors</h3>
<a class="sb {{'on' if sector=='all'}}" href="?sector=all"><span class="dot" style="background:#f85149"></span>All Markets</a>
<a class="sb {{'on' if sector=='h2h'}}" href="?sector=h2h"><span class="dot" style="background:#bc8cff"></span>H2H / 1X2</a>
<a class="sb {{'on' if sector=='dc'}}" href="?sector=dc"><span class="dot" style="background:#58a6ff"></span>Double Chance</a>
<a class="sb {{'on' if sector=='btts'}}" href="?sector=btts"><span class="dot" style="background:#d29922"></span>BTTS</a>
<a class="sb {{'on' if sector=='over_1.5'}}" href="?sector=over_1.5"><span class="dot" style="background:#3fb950"></span>Over 1.5</a>
<a class="sb {{'on' if sector=='over_2.5'}}" href="?sector=over_2.5"><span class="dot" style="background:#f85149"></span>Over 2.5</a>
<div style="margin-top:24px"><h3>Accuracy</h3>
{% for s in sa %}<div style="font-size:11px;color:#8b949e;margin-top:8px">{{s.n}} <span style="float:right;color:#e6edf3">{{s.a}}%</span></div>
<div class="ab"><div class="af" style="width:{{s.a}}%;background:{{s.c}}"></div></div>{% endfor %}</div>
</div>
<div class="mn">
<div class="hd"><div><h1>Lignes SPE | V8 Engine</h1><div style="font-size:12px;color:#8b949e;margin-top:2px">{{sn}}</div></div><div class="st"><span class="dot" style="background:#3fb950"></span>Autonomous</div></div>
<div class="sts">
<div class="st2"><div class="sl">Matches</div><div class="sv">{{mt|cm}}</div></div>
<div class="st2"><div class="sl">Signals</div><div class="sv">{{sc2}}</div></div>
<div class="st2"><div class="sl">Odds</div><div class="sv">{{od|cm}}</div></div>
<div class="st2"><div class="sl">Accuracy</div><div class="sv">{{ca}}%</div></div>
</div>
<div class="tabs">
<div class="tab on" onclick="stab('alpha')">Live alpha</div>
<div class="tab" onclick="stab('soode')">Cascading SOODE</div>
<div class="tab" onclick="stab('refined')">Refined alpha × SOODE</div>
<div class="tab" onclick="stab('matrix')">Weaponized matrix</div>
</div>
<div class="pn on" id="p-alpha">
<div class="fi"><input placeholder="Search team..." oninput="filt(this,'at')"></div>
<table id="at"><thead><tr><th data-type="date">Date / Time</th><th>Match</th><th>League</th><th>Market</th><th>Selection</th><th data-type="num">SPE %</th></tr></thead><tbody>
{% for s in al %}<tr>
<td data-v="{{s.match_date|td}}" style="white-space:nowrap;font-size:11px">{{s.match_date|td}}</td>
<td style="font-weight:500">{{s.home_team}} vs {{s.away_team}}</td>
<td style="font-size:11px;color:#8b949e">{{s.league}}</td>
<td>{% if s.market_type=='dc' %}<span class="p pb">DC</span>{% elif s.market_type=='h2h' %}<span class="p pp">H2H</span>{% elif s.market_type=='btts' %}<span class="p py">BTTS</span>{% else %}<span class="p pg">{{s.market_type}}</span>{% endif %}</td>
<td>{{s.predicted_outcome}}</td>
<td data-v="{{s.spe_implied_prob}}" class="m">{{s.spe_implied_prob}}</td>
</tr>{% endfor %}</tbody></table></div>

<div class="pn" id="p-matrix">
{% for p in pl %}
<div class="pc"><div class="ph"><span class="p pg">{{p.grade}}</span><span style="font-weight:500;font-size:13px">{{p.pid}}</span><span style="font-size:12px;color:#8b949e;margin-left:auto">{{p.legs|length}}-leg | adj={{p.adj}}%</span></div>
<table><thead><tr><th>#</th><th>Match</th><th>Market</th><th>Selection</th><th data-type="num">SPE</th></tr></thead><tbody>
{% for l in p.legs %}<tr><td>{{loop.index}}</td><td>{{l.home_team}} vs {{l.away_team}}</td><td>{{l.market_type}}</td><td>{{l.selection}}</td><td data-v="{{l.spe}}" class="m">{{l.spe}}</td></tr>{% endfor %}</tbody></table></div>
{% endfor %}
{% if not pl %}<div style="color:#8b949e;padding:20px;text-align:center">No parlays</div>{% endif %}
</div>

<div class="pn" id="p-soode">
<div class="fi"><input placeholder="Search team..." oninput="fsoode(this.value)">
<select onchange="fdiag(this.value)"><option value="all">All</option><option value="Stable">Stable</option><option value="Surging">Surging</option><option value="Micro">Micro-Shock</option><option value="Decline">Decline</option></select></div>
<div class="sg" id="sgg">
{% for s in so %}<div class="sc" data-n="{{s.name|lower}}" data-d="{{s.diag}}">
<div style="font-weight:500;font-size:13px;margin-bottom:4px">{{s.name}}</div>
{% if 'Stable' in s.diag %}<span class="p pg">Stable</span>{% elif 'Surging' in s.diag %}<span class="p pb">Surging</span>{% elif 'Micro' in s.diag %}<span class="p py">Micro-shock</span>{% elif 'Decline' in s.diag %}<span class="p pr">Decline</span>{% endif %}
{% set cl='#3fb950' if 'Stable' in s.diag else '#58a6ff' if 'Surging' in s.diag else '#d29922' if 'Micro' in s.diag else '#f85149' %}
<div style="margin-top:8px">{% for g in [('Micro',s.micro),('Meso',s.meso),('Macro',s.macro),('DNA',s.dna)] %}
<div class="gr"><span>{{g[0]}}</span><span>{{g[1]}}</span></div>
<div class="gb"><div class="gf" style="width:{{(g[1]*150)|int}}%;background:{{cl}}"></div></div>{% endfor %}</div></div>{% endfor %}</div></div>

<div class="pn" id="p-refined">
<div class="fi"><input placeholder="Search team..." oninput="filt(this,'rt')"></div>
<table id="rt"><thead><tr><th>Match</th><th>Matchup</th><th data-type="num">Mod</th><th>Market</th><th>Selection</th><th data-type="num">Refined SPE</th></tr></thead><tbody>
{% for r in rf %}<tr>
<td style="font-weight:500">{{r.home}} vs {{r.away}}</td>
<td>{% if 'Surging' in r.matchup %}<span class="p pb">{{r.matchup}}</span>{% elif 'Micro' in r.matchup %}<span class="p py">{{r.matchup}}</span>{% elif 'Decline' in r.matchup %}<span class="p pr">{{r.matchup}}</span>{% else %}<span class="p pg">{{r.matchup}}</span>{% endif %}</td>
<td data-v="{{r.modifier}}" class="m" style="text-align:center">{{r.modifier}}x</td>
<td>{{r.market}}</td><td>{{r.selection}}</td>
<td data-v="{{r.refined_spe}}" class="m">{{r.refined_spe}}</td></tr>{% endfor %}</tbody></table></div>
</div></div>

<script>
function stab(id){document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('on')});document.querySelectorAll('.pn').forEach(function(p){p.classList.remove('on')});event.target.classList.add('on');document.getElementById('p-'+id).classList.add('on')}
function filt(el,tid){var q=el.value.toLowerCase();document.querySelectorAll('#'+tid+' tbody tr').forEach(function(r){r.style.display=r.textContent.toLowerCase().indexOf(q)>=0?'':'none'})}
function fsoode(q){q=q.toLowerCase();document.querySelectorAll('#sgg .sc').forEach(function(c){c.style.display=c.getAttribute('data-n').indexOf(q)>=0?'':'none'})}
function fdiag(d){document.querySelectorAll('#sgg .sc').forEach(function(c){c.style.display=(d==='all'||c.getAttribute('data-d').indexOf(d)>=0)?'':'none'})}

/* SORTING: click any th to sort that column */
document.addEventListener('DOMContentLoaded',function(){
  var tables=document.querySelectorAll('table');
  for(var t=0;t<tables.length;t++){
    var ths=tables[t].querySelectorAll('thead th');
    for(var h=0;h<ths.length;h++){
      (function(th,colIdx,tbl){
        var dir='none';
        th.addEventListener('click',function(){
          var tbody=tbl.querySelector('tbody');
          if(!tbody)return;
          var rows=[];
          for(var i=0;i<tbody.rows.length;i++){rows.push(tbody.rows[i])}
          dir=(dir==='asc')?'desc':'asc';
          /* reset arrows */
          var allTh=tbl.querySelectorAll('thead th');
          for(var j=0;j<allTh.length;j++){allTh[j].textContent=allTh[j].textContent.replace(/ ▲/g,'').replace(/ ▼/g,'')}
          th.textContent+=(dir==='asc')?' ▲':' ▼';

          rows.sort(function(a,b){
            var cellA=a.cells[colIdx];
            var cellB=b.cells[colIdx];
            if(!cellA||!cellB)return 0;
            /* use data-v attribute if present, otherwise innerText */
            var va=cellA.getAttribute('data-v')||cellA.innerText.trim();
            var vb=cellB.getAttribute('data-v')||cellB.innerText.trim();
            /* detect date: YYYY-MM-DD */
            if(va.length===10 && va.charAt(4)==='-' && va.charAt(7)==='-'){
              if(dir==='asc')return va<vb?-1:va>vb?1:0;
              return va>vb?-1:va<vb?1:0;
            }
            /* numeric */
            var na=parseFloat(va);
            var nb=parseFloat(vb);
            if(!isNaN(na)&&!isNaN(nb)){
              return dir==='asc'?na-nb:nb-na;
            }
            /* string */
            if(dir==='asc')return va.localeCompare(vb);
            return vb.localeCompare(va);
          });
          for(var k=0;k<rows.length;k++){tbody.appendChild(rows[k])}
        });
      })(ths[h],h,tables[t]);
    }
  }
});
</script>
</body></html>"""

@app.template_filter('cm')
def cm(v):
    try:return f"{int(v):,}"
    except:return v

@app.template_filter('td')
def td(v):
    s=str(v)
    if len(s)>10:return s[:16].replace('T',' ')
    return s

SN={"all":"All Markets","h2h":"H2H (1X2)","dc":"Double Chance","btts":"BTTS","over_1.5":"Over 1.5","over_2.5":"Over 2.5"}
SC={"h2h":"#bc8cff","dc":"#58a6ff","btts":"#d29922","over_1.5":"#3fb950","over_2.5":"#f85149"}
SA={"h2h":54.2,"dc":77.4,"btts":60.1,"over_1.5":81.8,"over_2.5":64.1}

@app.route("/")
def dash():
    sector=request.args.get("sector","all")
    conn=gc();cur=conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT COUNT(*) AS c FROM matches");mt=cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM odds_history");od=cur.fetchone()["c"]
    mf="" if sector=="all" else f"AND la.market_type='{sector}'"
    cur.execute(f"SELECT la.match_date,la.home_team,la.away_team,la.league,la.market_type,la.predicted_outcome,la.spe_implied_prob FROM live_alpha la WHERE 1=1 {mf} ORDER BY la.spe_implied_prob DESC LIMIT 200")
    al=cur.fetchall()
    if sector=="all":cur.execute("SELECT DISTINCT parlay_id,risk_grade,adjusted_cumulative FROM weaponized_matrix ORDER BY adjusted_cumulative DESC LIMIT 20")
    else:cur.execute("SELECT DISTINCT parlay_id,risk_grade,adjusted_cumulative FROM weaponized_matrix WHERE market_type=%s ORDER BY adjusted_cumulative DESC LIMIT 20",(sector,))
    pm=cur.fetchall();pl=[]
    used_match_keys=set()
    for p in pm:
        cur.execute("SELECT wm.selection,wm.market_type,wm.spe_implied_prob,la.home_team,la.away_team FROM weaponized_matrix wm LEFT JOIN live_alpha la ON wm.alpha_id=la.id WHERE wm.parlay_id=%s ORDER BY wm.leg_number",(p["parlay_id"],))
        raw_legs=cur.fetchall()
        legs=[];skip_parlay=False
        for l in raw_legs:
            mk=f"{l.get('home_team','')}-{l.get('away_team','')}-{l['market_type']}"
            if mk in used_match_keys:skip_parlay=True;break
            legs.append({"selection":l["selection"],"market_type":l["market_type"],"spe":float(l["spe_implied_prob"]),"home_team":l.get("home_team",""),"away_team":l.get("away_team","")})
        if skip_parlay or not legs:continue
        for l in raw_legs:
            mk=f"{l.get('home_team','')}-{l.get('away_team','')}-{l['market_type']}"
            used_match_keys.add(mk)
        pl.append({"pid":p["parlay_id"],"grade":p["risk_grade"] or "A","adj":float(p["adjusted_cumulative"] or 0),"legs":legs})
    cur.execute("SELECT t.name,s.micro_grip,s.meso_grip,s.macro_grip,s.dna_grip,s.system_diagnosis FROM soode_keys s JOIN teams t ON s.team_id=t.team_id ORDER BY s.dna_grip ASC")
    so=[{"name":r["name"],"micro":float(r["micro_grip"]),"meso":float(r["meso_grip"]),"macro":float(r["macro_grip"]),"dna":float(r["dna_grip"]),"diag":r["system_diagnosis"]} for r in cur.fetchall()]
    cur.execute(f"SELECT la.home_team,la.away_team,ra.matchup_class,ra.kelly_modifier,la.market_type,la.predicted_outcome,ra.refined_spe FROM refined_alpha ra JOIN live_alpha la ON ra.alpha_id=la.id WHERE 1=1 {mf} ORDER BY ra.refined_spe DESC LIMIT 200")
    rf=[{"home":r["home_team"],"away":r["away_team"],"matchup":r["matchup_class"],"modifier":float(r["kelly_modifier"]),"market":r["market_type"],"selection":r["predicted_outcome"],"refined_spe":float(r["refined_spe"])} for r in cur.fetchall()]
    cur.close();conn.close()
    return render_template_string(T,mt=mt,od=od,al=al,pl=pl,so=so,rf=rf,sector=sector,sn=SN.get(sector,"All"),
        sc2=len(al),ca=SA.get(sector,68.2) if sector!="all" else 68.2,
        sa=[{"n":SN[k],"a":v,"c":SC.get(k,"#3fb950")} for k,v in SA.items()])

@app.route("/health")
def health():
    try:c=gc();c.cursor().execute("SELECT 1");c.close();return"ok",200
    except:return"unhealthy",503

if __name__=="__main__":
    app.run(host="0.0.0.0",port=8080)
