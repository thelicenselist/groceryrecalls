"""
groceryrecalls.com alert bot — runs after fetch_recalls.py in the daily
GitHub Action and writes ready-to-paste emails as GitHub issues.

  NEW ALERT      any Ongoing recall_number not in data/state.json
                 -> one combined "SEND: recall alert" issue
  WEEKLY DIGEST  every Monday (UTC) -> "SEND: weekly digest" issue
  otherwise      nothing is filed

Reads the raw FDA results fetch_recalls.py saved to data/latest/<slug>.json.
A store with no entry in state.json yet is seeded silently (no alert).

  python alert_bot.py              dry run: print what would be filed
  python alert_bot.py --post       file issues with `gh`, then save state
  python alert_bot.py --seed       record today's Ongoing recalls as known
"""
import argparse, json, os, subprocess, sys, tempfile
from datetime import datetime, timezone, date

from fetch_recalls import RETAILERS, parse_product, fmt_date

SITE = "https://groceryrecalls.com"

STORE_INFO = {
    "costco": {
        "name": "Costco",
        "refund": ("Bring the item — or just your membership card — to any Costco "
                   "returns desk. Refunds on recalled items are typically issued "
                   "without a receipt."),
    },
    "walmart": {
        "name": "Walmart",
        "refund": ("Bring the item to any Walmart service desk for a full refund — "
                   "a receipt helps, but recalls are typically honored without one."),
    },
}

CLASS_MEANING = {  # FDA's own definitions, in plain words
    "class i": "the most serious type — could cause serious health problems",
    "class ii": "could cause temporary or medically reversible health problems",
    "class iii": "unlikely to cause health problems",
}

DISCLAIMER = ("Details come from the FDA's public recall database. Always follow "
              "the official recall notice. GroceryRecalls.com is independent and "
              "not affiliated with any retailer or government agency.")


def store(slug):
    info = STORE_INFO.get(slug, {})
    return {
        "name": info.get("name", slug.capitalize()),
        "refund": info.get("refund", "Check with the store where you bought it "
                                     "about returning it for a refund."),
        "url": f"{SITE}/{slug}/",
    }

def join_names(names, conj="and"):
    names = list(names)
    if len(names) <= 1: return "".join(names)
    if len(names) == 2: return f"{names[0]} {conj} {names[1]}"
    return ", ".join(names[:-1]) + f", {conj} {names[-1]}"

def nice_date(d):
    return f"{d:%B} {d.day}, {d.year}"

def clip(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"

def load_latest(latest_dir, slug):
    path = os.path.join(latest_dir, f"{slug}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("results") or []

def ongoing(results):
    recs = [r for r in results
            if (r.get("status") or "").lower() == "ongoing" and r.get("recall_number")]
    return sorted(recs, key=lambda r: r.get("recall_initiation_date") or "", reverse=True)


# ---------------------------------------------------------------- emails

def recall_block(rec, store_names):
    title, sizes, upcs = parse_product(rec.get("product_description"))
    lines = [f"PRODUCT: {title}",
             f"Sold at: {join_names(store_names)}",
             f"Why it was recalled: {clip(rec.get('reason_for_recall'), 400)}"]
    ids = list(sizes) + [f"UPC {u}" for u in upcs]
    if ids:
        lines.append("Sizes / UPCs: " + " · ".join(ids))
    lines.append(f"Lot / code info: {clip(rec.get('code_info') or 'see the official notice', 300)}")
    if rec.get("recalling_firm"):
        lines.append(f"Recalled by: {rec['recalling_firm']}")
    started = f"Recall started: {fmt_date(rec.get('recall_initiation_date'))}"
    cls = (rec.get("classification") or "").strip()
    if cls:
        meaning = CLASS_MEANING.get(cls.lower())
        started += f" · FDA {cls}" + (f" ({meaning})" if meaning else "")
    lines.append(started)
    return "\n".join(lines)

def alert_email(new_items, today):
    """new_items: list of (rec, [slugs]) — one entry per recall_number."""
    slugs = []
    for _, s in new_items:
        slugs += [x for x in s if x not in slugs]
    names = [store(s)["name"] for s in slugs]
    n = len(new_items)

    if n == 1:
        title, _, _ = parse_product(new_items[0][0].get("product_description"))
        subject = f"Recall alert: {clip(title, 60)} ({join_names(names)})"
        intro = f"A new food recall affects a product sold at {join_names(names)}. Here are the details."
    else:
        subject = f"Recall alert: {n} new food recalls at {join_names(names)}"
        intro = f"{n} new food recalls affect products sold at {join_names(names)}. Here are the details."

    blocks = "\n\n—\n\n".join(recall_block(rec, [store(s)["name"] for s in ss])
                              for rec, ss in new_items)
    it = "this product" if n == 1 else "these products"
    todo = [f"• Check your kitchen, fridge, and freezer for {it}, and compare the sizes, UPCs, and lot codes above.",
            "• If you have it, don't eat it. If someone in your home ate it and feels unwell, call your doctor."]
    todo += [f"• Refund at {store(s)['name']}: {store(s)['refund']}" for s in slugs]
    links = "\n".join(f"{store(s)['name']}: {store(s)['url']}" for s in slugs)

    body = (f"Hi there,\n\n{intro}\n\n{blocks}\n\n"
            f"What to do\n" + "\n".join(todo) + "\n\n"
            f"See every active and past recall:\n{links}\n\n"
            f"Stay safe,\nGroceryRecalls.com\n\n{DISCLAIMER}")
    return subject, body

def digest_email(active_by_slug, missing, today):
    slugs = [s for s, _ in RETAILERS]
    names = [store(s)["name"] for s in slugs]
    subject = f"This week's grocery recalls: {join_names(names, '&')} ({today:%b} {today.day})"

    parts = []
    if not missing and not any(active_by_slug.get(s) for s in slugs):
        parts.append(f"Nothing active at {join_names(names, 'or')} this week — good news.")
    else:
        for s in slugs:
            st = store(s)
            if s in missing:
                parts.append(f"{st['name'].upper()}: we couldn't load today's data. "
                             f"Check the latest list: {st['url']}")
                continue
            recs = active_by_slug.get(s) or []
            if not recs:
                parts.append(f"{st['name'].upper()}: nothing active right now — good news.")
                continue
            lines = [f"{st['name'].upper()}: {len(recs)} active recall{'s' if len(recs) != 1 else ''}"]
            for rec in recs[:3]:
                title, _, _ = parse_product(rec.get("product_description"))
                lines.append(f"• {clip(title, 80)} — {clip(rec.get('reason_for_recall'), 140)} "
                             f"(since {fmt_date(rec.get('recall_initiation_date'))})")
            if len(recs) > 3:
                lines.append(f"• …and {len(recs) - 3} more")
            lines.append(f"Full {st['name']} list: {st['url']}")
            parts.append("\n".join(lines))

    body = (f"Hi there,\n\nHere's your weekly recall check for {join_names(names)}.\n\n"
            + "\n\n".join(parts) + "\n\n"
            f"Know someone who shops at {join_names(names, 'or')}? Forward this email — "
            f"they can get these alerts free at groceryrecalls.com.\n\n"
            f"Stay safe,\nGroceryRecalls.com\n\n{DISCLAIMER}")
    return subject, body

def issue_body(subject, body, note):
    fence = "````"
    return (f"**Ready to send.** Copy the subject and body into beehiiv, give it a "
            f"quick read, and send. Close this issue once it's sent.\n\n"
            f"**Subject**\n{fence}text\n{subject}\n{fence}\n\n"
            f"**Email body**\n{fence}text\n{body}\n{fence}\n\n"
            f"<sub>{note}</sub>\n")


# ---------------------------------------------------------------- main

def file_issue(title, body):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(body)
        path = f.name
    try:
        r = subprocess.run(["gh", "issue", "create", "--title", title, "--body-file", path],
                           capture_output=True, text=True, encoding="utf-8")
    finally:
        os.unlink(path)
    if r.returncode != 0:
        raise RuntimeError(f"gh issue create failed: {r.stderr.strip()}")
    return r.stdout.strip()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", action="store_true", help="file issues with gh and save state")
    ap.add_argument("--seed", action="store_true", help="record current Ongoing recalls, file nothing")
    ap.add_argument("--state", default="data/state.json")
    ap.add_argument("--latest-dir", default="data/latest")
    ap.add_argument("--today", help="YYYY-MM-DD override, for testing the Monday digest")
    ap.add_argument("--title-prefix", default="", help="prepended to issue titles (testing)")
    ap.add_argument("--force-digest", action="store_true", help="file the digest even if not Monday")
    args = ap.parse_args()

    today = (date.fromisoformat(args.today) if args.today
             else datetime.now(timezone.utc).date())

    state = None
    if os.path.exists(args.state):
        with open(args.state, encoding="utf-8") as f:
            state = json.load(f)

    new_state, active_by_slug, missing, seeded = {}, {}, [], []
    new = {}  # recall_number -> (rec, [slugs])
    for slug, _ in RETAILERS:
        results = load_latest(args.latest_dir, slug)
        if results is None:
            missing.append(slug)
            if state and slug in state:
                new_state[slug] = state[slug]  # keep what we knew; don't re-alert tomorrow
            print(f"{slug}: no data in {args.latest_dir} — state left as it was")
            continue
        recs = ongoing(results)
        active_by_slug[slug] = recs
        new_state[slug] = sorted({r["recall_number"] for r in recs})
        if args.seed or state is None or slug not in state:
            seeded.append(slug)
            continue
        known = set(state[slug])
        for r in recs:
            if r["recall_number"] not in known:
                entry = new.setdefault(r["recall_number"], (r, []))
                entry[1].append(slug)

    for slug in seeded:
        print(f"{slug}: seeded with {len(new_state[slug])} Ongoing recall(s), no alert")

    issues = []
    if new and not args.seed:
        items = list(new.values())
        subject, body = alert_email(items, today)
        stores_in_alert = []
        for _, ss in items:
            stores_in_alert += [store(s)["name"] for s in ss if store(s)["name"] not in stores_in_alert]
        title = f"SEND: recall alert — {', '.join(stores_in_alert)}, {nice_date(today)}"
        note = "Filed by alert_bot.py · new recall numbers: " + ", ".join(sorted(new))
        issues.append((title, issue_body(subject, body, note), subject, body))
    if (today.weekday() == 0 or args.force_digest) and not args.seed:
        subject, body = digest_email(active_by_slug, missing, today)
        title = f"SEND: weekly digest, {nice_date(today)}"
        issues.append((title, issue_body(subject, body, "Filed by alert_bot.py · Monday digest"),
                       subject, body))

    if not issues:
        print(f"{today.isoformat()}: no new recalls and not Monday — nothing to send.")

    for title, ibody, subject, body in issues:
        title = args.title_prefix + title
        if args.post:
            print(f"Filed: {file_issue(title, ibody)}  ({title})")
        else:
            print(f"\n===== WOULD FILE ISSUE: {title} =====\n{ibody}")

    if args.post or args.seed:
        if new_state != state:
            os.makedirs(os.path.dirname(args.state) or ".", exist_ok=True)
            with open(args.state, "w", encoding="utf-8") as f:
                json.dump(new_state, f, indent=1, sort_keys=True)
                f.write("\n")
            print(f"Saved {args.state}")
        else:
            print(f"{args.state} unchanged")
    else:
        print("(dry run — state not saved; use --post or --seed)")

if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:  # issue filing failed: state NOT saved, so tomorrow retries
        sys.exit(str(e))
