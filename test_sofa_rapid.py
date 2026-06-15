import urllib.request
import json
from src.config import RAPIDAPI_KEY, RAPIDAPI_HOST

req = urllib.request.Request(f"https://{RAPIDAPI_HOST}/api/v1/sport/football/scheduled-events/2026-06-15", headers={
    "X-RapidAPI-Key": RAPIDAPI_KEY,
    "X-RapidAPI-Host": RAPIDAPI_HOST
})

try:
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    print(len(data.get("events", [])))
except Exception as e:
    print(e)
