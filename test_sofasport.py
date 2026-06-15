import urllib.request
import json
import os

from dotenv import load_dotenv
load_dotenv('.env')
key = os.getenv("RAPIDAPI_KEY")

host = "sofasport.p.rapidapi.com"
url = f"https://{host}/v1/events/schedule/date?date=2026-06-15&sport=tennis"

req = urllib.request.Request(url, headers={
    "X-RapidAPI-Key": key,
    "X-RapidAPI-Host": host
})

try:
    resp = urllib.request.urlopen(req, timeout=5)
    data = json.loads(resp.read().decode("utf-8"))
    print(f"SUCCESS")
except Exception as e:
    print(f"FAILED: {e}")
