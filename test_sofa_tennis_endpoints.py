import urllib.request
import json
import os

from dotenv import load_dotenv
load_dotenv('.env')
key = os.getenv("RAPIDAPI_KEY")

endpoints = [
    "/matches/v1/list-by-date?category=tennis&date=2026-06-15",
    "/v1/events/schedule/date?date=2026-06-15&sport=tennis",
    "/v1/events/schedule/date?date=2026-06-15",  # See if it returns everything
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
    except Exception as e:
        print(f"FAILED {ep}: {e}")
