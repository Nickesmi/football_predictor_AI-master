import urllib.request
import json
import os

from dotenv import load_dotenv
load_dotenv('.env')
key = os.getenv("API_FOOTBALL_KEY")

url = "https://v3.tennis.api-sports.io/fixtures?date=2026-06-15"

req = urllib.request.Request(url, headers={
    "x-apisports-key": key
})

try:
    resp = urllib.request.urlopen(req, timeout=5)
    data = json.loads(resp.read().decode("utf-8"))
    print(f"SUCCESS: {len(data.get('response', []))} fixtures found")
    if data.get('response'):
        f = data['response'][0]
        print(f"Sample: {f['teams']['home']['name']} vs {f['teams']['away']['name']}")
except Exception as e:
    print(f"FAILED: {e}")
