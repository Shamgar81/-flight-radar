# flight_agent.py -- cloud version with Telegram control (mailbox model)
import json
import os
import requests
from datetime import datetime, timedelta
from fast_flights import FlightQuery, Passengers, create_query, get_flights

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

ROUTES_FILE = "routes.json"
MEMORY_FILE = "last_prices.json"
OFFSET_FILE = "bot_offset.json"

# used only the very first time, to seed routes.json
DEFAULT_ROUTES = [
    {"label": "TLV -> New York", "from": "TLV", "to": "NYC",
     "dates": ["2027-04-11", "2027-04-12"], "threshold": 450},
    {"label": "Toronto -> TLV", "from": "YYZ", "to": "TLV",
     "dates": ["2027-05-02", "2027-05-03"], "threshold": 450},
]


# ---------------- small JSON helpers ----------------
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------- telegram ----------------
def tg(method, **params):
    url = "https://api.telegram.org/bot" + TOKEN + "/" + method
    return requests.post(url, data=params, timeout=25).json()


def send_telegram(text):
    tg("sendMessage", chat_id=CHAT_ID, text=text)


# ---------------- commands ----------------
def routes_summary(routes):
    if not routes:
        return "No routes monitored. Add one:\n/add TLV NYC 2027-04-11 450"
    lines = ["Monitored routes:"]
    for i, r in enumerate(routes, 1):
        lines.append(str(i) + ". " + r["from"] + " -> " + r["to"]
                     + "  " + " / ".join(r["dates"]) + "  (<= $" + str(r["threshold"]) + ")")
    return "\n".join(lines)


def handle_command(text, routes):
    """Return (reply_or_None, changed_bool). Mutates 'routes' in place."""
    parts = text.strip().split()
    if not parts:
        return None, False
    cmd = parts[0].lower().split("@")[0]  # strip @botname if present

    if cmd in ("/start", "/help"):
        return ("Flight radar bot:\n"
                "/list - show routes\n"
                "/add FROM TO YYYY-MM-DD PRICE - add a route\n"
                "/remove N - remove route number N"), False

    if cmd == "/list":
        return routes_summary(routes), False

    if cmd == "/add":
        if len(parts) != 5:
            return "Usage: /add FROM TO YYYY-MM-DD PRICE\nex: /add TLV NYC 2027-04-11 450", False
        _, frm, to, date, thr = parts
        frm, to = frm.upper(), to.upper()
        try:
            d = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return "Bad date. Use YYYY-MM-DD (e.g. 2027-04-11)", False
        if not thr.isdigit():
            return "Price must be a number (e.g. 450)", False
        next_day = (d + timedelta(days=1)).strftime("%Y-%m-%d")
        routes.append({"label": frm + " -> " + to, "from": frm, "to": to,
                       "dates": [date, next_day], "threshold": int(thr)})
        return ("Added: " + frm + " -> " + to + " on " + date
                + " (+ " + next_day + ")\nAlert under $" + thr), True

    if cmd == "/remove":
        if len(parts) != 2 or not parts[1].isdigit():
            return "Usage: /remove N  (number from /list)", False
        idx = int(parts[1])
        if idx < 1 or idx > len(routes):
            return "No route #" + str(idx) + ". Use /list.", False
        gone = routes.pop(idx - 1)
        return "Removed: " + gone["from"] + " -> " + gone["to"], True

    return None, False  # unknown -> ignore silently


def process_updates(routes):
    state = load_json(OFFSET_FILE, {"last_update_id": 0})
    last_id = state.get("last_update_id", 0)
    params = {"timeout": 0}
    if last_id:
        params["offset"] = last_id + 1
    resp = tg("getUpdates", **params)
    if not resp.get("ok"):
        print("  getUpdates failed:", resp)
        return
    changed = False
    max_id = last_id
    for upd in resp.get("result", []):
        max_id = max(max_id, upd["update_id"])
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            continue
        text = msg.get("text", "")
        if not text.startswith("/"):
            continue
        reply, ch = handle_command(text, routes)
        if reply:
            send_telegram(reply)
        if ch:
            changed = True
    if max_id != last_id:
        save_json(OFFSET_FILE, {"last_update_id": max_id})
    if changed:
        save_json(ROUTES_FILE, routes)


# ---------------- price checking ----------------
def cheapest_for_route(route):
    best = None
    for date in route["dates"]:
        try:
            query = create_query(
                flights=[FlightQuery(date=date, from_airport=route["from"], to_airport=route["to"])],
                trip="one-way", seat="economy",
                passengers=Passengers(adults=1),
                currency="USD", checked_bags=1,
            )
            results = get_flights(query)
            options = [f for f in results if f.price]
            if not options:
                continue
            c = min(options, key=lambda f: f.price)
            airline = ", ".join(c.airlines) if c.airlines else "?"
            if best is None or c.price < best[0]:
                best = (c.price, date, airline)
        except Exception as e:
            print("  (error on " + date + ": " + type(e).__name__ + ")")
    return best


def check_prices(routes):
    memory = load_json(MEMORY_FILE, {})
    for route in routes:
        print("Checking " + route["label"] + " ...")
        best = cheapest_for_route(route)
        if best is None:
            print("  no price found")
            continue
        price, date, airline = best
        print("  cheapest: $" + str(price) + " on " + date + " (" + airline + ")")
        key = route["from"] + route["to"] + ":" + ",".join(route["dates"])
        last_low = memory.get(key)
        is_new_low = (last_low is None) or (price < last_low)
        if price <= route["threshold"] and is_new_low:
            msg = ("\u2708\ufe0f \u05de\u05e6\u05d9\u05d0\u05d4!\n" + route["from"] + " -> " + route["to"] + "\n"
                   + "$" + str(price) + "  |  " + date + "\n" + airline + "\n"
                   + "\u05de\u05ea\u05d7\u05ea \u05dc\u05d9\u05e2\u05d3 ($" + str(route["threshold"]) + ")")
            send_telegram(msg)
            memory[key] = price
            print("  -> ALERT SENT")
        elif price <= route["threshold"]:
            print("  -> below target but not a new low (no alert)")
        else:
            print("  -> above target (no alert)")
    save_json(MEMORY_FILE, memory)


def main():
    routes = load_json(ROUTES_FILE, None)
    if routes is None:
        routes = [dict(r) for r in DEFAULT_ROUTES]
        save_json(ROUTES_FILE, routes)
        print("Seeded routes.json with defaults.")
    process_updates(routes)   # read & apply any new Telegram commands
    check_prices(routes)      # then check prices
    print("Done.")


if __name__ == "__main__":
    main()
