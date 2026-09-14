"""
groceryrecalls.com updater — pulls Costco food recalls from the FDA's
free public API and bakes them into the Costco page's HTML.
Runs on a schedule via GitHub Actions. No credentials needed.
"""
import json, re, urllib.request
from datetime import datetime, timezone

API = ("https://api.fda.gov/food/enforcement.json"
       "?search=(distribution_pattern:costco+OR+recalling_firm:costco)"
       "&sort=recall_initiation_date:desc&limit=25")

def fmt_date(d):  # FDA dates look like 20260406
    try: return datetime.strptime(d, "%Y%m%d").strftime("%B %d, %Y")
    except Exception: return d or "date unknown"

def esc(s):
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

with urllib.request.urlopen(API, timeout=60) as r:
    data = json.load(r)

cards = []
for rec in data.get("results", []):
    status = esc(rec.get("status",""))
    badge = "badge-active" if status.lower() == "ongoing" else "badge-done"
    cards.append(f"""
    <article class="recall">
      <div class="recall-head">
        <span class="badge {badge}">{esc(status) or 'Status unknown'}</span>
        <time>{fmt_date(rec.get('recall_initiation_date'))}</time>
      </div>
      <h3>{esc(rec.get('product_description','Unnamed product'))[:200]}</h3>
      <p class="reason"><strong>Why:</strong> {esc(rec.get('reason_for_recall',''))[:400]}</p>
      <p class="meta">Recalled by {esc(rec.get('recalling_firm',''))} &middot;
         Class {esc(rec.get('classification','?')).replace('Class ','')} &middot;
         Lot/code info: {esc(rec.get('code_info','see official notice'))[:150]}</p>
    </article>""")

updated = datetime.now(timezone.utc).strftime("%B %d, %Y")
tpl = open("costco/template.html", encoding="utf-8").read()
html = tpl.replace("<!--RECALLS-->", "\n".join(cards) if cards
                   else "<p class='allclear'>No Costco food recalls in the current FDA feed. Good news.</p>")
html = html.replace("<!--UPDATED-->", updated)
html = html.replace("<!--COUNT-->", str(len(cards)))
open("costco/index.html","w", encoding="utf-8").write(html)
print(f"Wrote costco/index.html — {len(cards)} recalls, updated {updated}")
