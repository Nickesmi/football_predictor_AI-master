import urllib.request
import json
import os

from dotenv import load_dotenv
load_dotenv('.env')
key = os.getenv("API_FOOTBALL_KEY")

endpoints = [
    "/games?date=2026-06-15",
    "/matches?date=2026-06-15",
    "/fixtures?date=2026-06-15",
    "/events?date=2026-06-15"
]

host = "v3.tennis.api-sports.io"

for ep in endpoints:
    url = f"https://{host}{ep}"
    req = urllib.request.Request(url, headers={
        "x-apisports-key": key
    })
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode("utf-8"))
        print(f"SUCCESS {ep}: {len(data.get('response', []))} items")
    except Exception as e:
        print(f"FAILED {ep}: {e}")
