"""
groceryrecalls.com updater — pulls Costco food recalls from the FDA's
free public API and bakes them into the Costco page's HTML.
Runs on a schedule via GitHub Actions. No credentials needed.
"""
import json, re, urllib.request, urllib.error
from datetime import datetime, timezone

API = ("https://api.fda.gov/food/enforcement.json"
       "?search=(distribution_pattern:costco+OR+recalling_firm:costco)"
       "&sort=recall_initiation_date:desc&limit=25")

def fmt_date(d):  # FDA dates look like 20260406
    try: return datetime.strptime(d, "%Y%m%d").strftime("%B %d, %Y")
    except Exception: return d or "date unknown"

def esc(s):
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

try:
    with urllib.request.urlopen(API, timeout=60) as r:
        data = json.load(r)
except urllib.error.HTTPError as e:
    if e.code != 404:  # openFDA answers "no matches" with a 404, not an empty list
        raise
    data = {"results": []}

import re as _re
ICONS = [
 # specific product types first
 ("madeleine","🥐"),("beignet","🥐"),("croissant","🥐"),("pastry","🥐"),
 ("bread","🥐"),("bakery","🥐"),("cookie","🍪"),("cake","🍰"),
 ("pizza","🍕"),("smoothie","🧃"),("cider","🧃"),("juice","🧃"),
 ("soup","🥫"),("sauce","🥫"),("dressing","🥫"),("salsa","🥫"),
 ("ice cream","🍨"),("yogurt","🥛"),("chocolate","🍫"),("candy","🍬"),
 ("granola","🌾"),("oat","🌾"),("oats","🌾"),("chia","🌾"),
 ("seed","🌾"),("seeds","🌾"),("cereal","🌾"),
 # proteins and produce
 ("chicken","🍗"),("poultry","🍗"),("turkey","🍗"),("beef","🥩"),
 ("steak","🥩"),("pork","🥓"),("bacon","🥓"),("salmon","🐟"),
 ("fish","🐟"),("tuna","🐟"),("seafood","🦐"),("shrimp","🦐"),
 ("salad","🥬"),("lettuce","🥬"),("greens","🥬"),("spinach","🥬"),
 ("broccoli","🥦"),("fruit","🍎"),("apple","🍎"),("berry","🫐"),
 ("berries","🫐"),("infant","🍼"),("formula","🍼"),
 # generic ingredients LAST so they only win when nothing above hits
 ("cheese","🧀"),("milk","🥛"),("dairy","🥛"),("butter","🧈"),
 ("egg","🥚"),("eggs","🥚"),("nut","🥜"),("peanut","🥜"),
 ("almond","🥜"),("frozen","🧊")]
def pick_icon(desc):
    d = (desc or "").lower()
    for kw, ico in ICONS:
        if _re.search(r"\b" + _re.escape(kw) + r"s?\b", d):
            return ico
    return "🛒"

def parse_product(desc):
    d = desc or "Unnamed product"
    upcs = _re.findall(r"UPC[:\s#]*([0-9][0-9 \-]{9,18}[0-9])", d)
    sizes = _re.findall(
        r"\b\d+(?:\.\d+)?\s?(?:fl\.? ?oz|oz|lb|lbs|g|kg|ml|mL|L|count|ct)\b\.?",
        d, _re.IGNORECASE)
    t = d.split(";")[0]
    for m in [" UPC"," upc"," Item #"," item #"," Item#",", net wt",
              " net wt"," Net Wt",", Net"]:
        i = t.find(m)
        if i > 8: t = t[:i]
    t = t.strip(" ,.-")
    if len(t) > 90: t = t[:90].rsplit(" ", 1)[0] + "…"
    return t, sizes[:3], upcs[:3]

cards = []
for rec in data.get("results", []):
    status = esc(rec.get("status",""))
    badge = "badge-active" if status.lower() == "ongoing" else "badge-done"
    title, sizes, upcs = parse_product(rec.get("product_description"))
    chips = "".join(f'<span class="chip">{esc(s)}</span>' for s in sizes)
    chips += "".join(f'<span class="chip">UPC {esc(u)}</span>' for u in upcs)
    cards.append(f"""
    <article class="recall">
      <div class="recall-head">
        <span class="badge {badge}">{esc(status) or 'Status unknown'}</span>
        <time>{fmt_date(rec.get('recall_initiation_date'))}</time>
      </div>
      <h3>{pick_icon(rec.get('product_description'))} {esc(title)}</h3>
      {f'<div class="chips">{chips}</div>' if chips else ''}
      <p class="reason"><strong>Why:</strong> {esc(rec.get('reason_for_recall',''))[:400]}</p>
      <details class="fulldesc"><summary>Full product details</summary>
        <p>{esc(rec.get('product_description',''))[:900]}</p></details>
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
