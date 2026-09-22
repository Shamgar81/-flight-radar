# flight_agent.py -- cloud version (reads secrets from environment)
import json
import os
import requests
from fast_flights import FlightQuery, Passengers, create_query, get_flights

# ---- secrets come from GitHub Secrets (not written in the code) ----
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Each route: label, from, to, list of dates (flexibility), threshold in USD
ROUTES = [
    {"label": "TLV -> New York", "from": "TLV", "to": "NYC",
     "dates": ["2027-04-11", "2027-04-12"], "threshold": 500},
    {"label": "Toronto -> TLV",  "from": "YYZ", "to": "TLV",
     "dates": ["2027-05-02", "2027-05-03"], "threshold": 500},
]

MEMORY_FILE = "last_prices.json"


def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_memory(mem):
    with open(MEMORY_FILE, "w") as f:
        json.dump(mem, f)


def cheapest_for_route(route):
    best = None  # (price, date, airline)
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


def send_telegram(text):
    url = "https://api.telegram.org/bot" + TOKEN + "/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=20)


def main():
    memory = load_memory()
    for route in ROUTES:
        print("Checking " + route["label"] + " ...")
        best = cheapest_for_route(route)
        if best is None:
            print("  no price found")
            continue
        price, date, airline = best
        print("  cheapest: $" + str(price) + " on " + date + " (" + airline + ")")

        key = route["label"]
        last_low = memory.get(key)
        is_new_low = (last_low is None) or (price < last_low)

        if price <= route["threshold"] and is_new_low:
            msg = (
                "\u2708\ufe0f \u05de\u05e6\u05d9\u05d0\u05d4!\n"
                + route["label"] + "\n"
                + "$" + str(price) + "  |  " + date + "\n"
                + airline + "\n"
                + "\u05de\u05ea\u05d7\u05ea \u05dc\u05d9\u05e2\u05d3 ($" + str(route["threshold"]) + ")"
            )
            send_telegram(msg)
            memory[key] = price
            print("  -> ALERT SENT")
        elif price <= route["threshold"]:
            print("  -> below target but not a new low (no alert)")
        else:
            print("  -> above target (no alert)")

    save_memory(memory)
    print("Done.")


if __name__ == "__main__":
    main()
