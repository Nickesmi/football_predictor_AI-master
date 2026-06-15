from curl_cffi import requests

try:
    res = requests.get("https://api.sofascore.com/api/v1/sport/football/scheduled-events/2026-06-15", impersonate="chrome110")
    print(res.status_code)
    if res.status_code == 200:
        data = res.json()
        print(f"Events: {len(data.get('events', []))}")
    else:
        print(res.text[:200])
except Exception as e:
    print(f"Error: {e}")
