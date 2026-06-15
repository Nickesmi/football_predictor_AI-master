import urllib.request
import os

from dotenv import load_dotenv
load_dotenv('.env')
key = os.getenv("RAPIDAPI_KEY")
host = "tennis-live-data.p.rapidapi.com"

url = f"https://{host}/matches/2026-06-15"
req = urllib.request.Request(url, headers={
    "X-RapidAPI-Key": key,
    "X-RapidAPI-Host": host
})
try:
    resp = urllib.request.urlopen(req, timeout=5)
    print("SUCCESS")
except Exception as e:
    print(f"FAILED: {e}")
