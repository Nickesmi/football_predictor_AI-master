import urllib.request
import json
import os
from dotenv import load_dotenv
load_dotenv('.env')

key = os.getenv("RAPIDAPI_KEY")
host = "sofascore.p.rapidapi.com"

endpoints = [
    "/sport/tennis/events/2026-06-15",
    "/api/v1/sport/tennis/events/schedule/date?date=2026-06-15",
    "/tournaments/get-schedule?date=2026-06-15&sport=tennis",
    "/matches/v1/events/schedule/date?date=2026-06-15&sport=tennis"
]

for ep in endpoints:
    url = f"https://{host}{ep}"
    req = urllib.request.Request(url, headers={
        "X-RapidAPI-Key": key,
        "X-RapidAPI-Host": host
    })
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        print(f"SUCCESS: {ep}")
    except Exception as e:
        print(f"FAILED {ep}: {e}")
