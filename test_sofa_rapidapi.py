import urllib.request
import json
import os

from dotenv import load_dotenv
load_dotenv('.env')
key = os.getenv("RAPIDAPI_KEY")

endpoints = [
    "/sport/tennis/scheduled-events/2026-06-15",
    "/api/v1/sport/tennis/scheduled-events/2026-06-15"
]

host = "sofascore.p.rapidapi.com"

for ep in endpoints:
    url = f"https://{host}{ep}"
    req = urllib.request.Request(url, headers={
        "X-RapidAPI-Key": key,
        "X-RapidAPI-Host": host
    })
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        print(f"SUCCESS: {ep}")
        data = json.loads(resp.read().decode("utf-8"))
        events = data.get("events") or data.get("data")
        print(f"  -> Found events type: {type(events)} length: {len(events) if events else 0}")
        if events and len(events) > 0:
            print(f"Sample: {events[0].get('homeTeam', {}).get('name')} vs {events[0].get('awayTeam', {}).get('name')}")
    except Exception as e:
        print(f"FAILED {ep}: {e}")
