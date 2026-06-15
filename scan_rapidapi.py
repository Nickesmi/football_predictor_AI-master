import urllib.request
import json

endpoints = [
    "/v1/events/schedule/date?date=2026-06-13",
    "/matches/v1/list-by-date?category=football&date=2026-06-13",
    "/matches/get-scheduled-events?date=2026-06-13",
    "/events/schedule/date?date=2026-06-13",
    "/sport/football/scheduled-events/2026-06-13",
    "/api/v1/sport/football/scheduled-events/2026-06-13",
    "/tournaments/get-schedule?date=2026-06-13",
    "/matches/v1/events/schedule/date?date=2026-06-13"
]

host = "sofascore.p.rapidapi.com"
key = "c0e43b61c1msh391b41f92aab30bp1b0e19jsn68d1602a35ee"

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
