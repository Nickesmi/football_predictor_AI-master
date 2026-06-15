import urllib.request
import json
import os

from dotenv import load_dotenv
load_dotenv('.env')
key = os.getenv("RAPIDAPI_KEY")

endpoints = [
    "/categories",
    "/sport/tennis/categories",
    "/api/v1/config/default-unique-tournaments/US/tennis",
    "/api/v1/sport/tennis/categories"
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
