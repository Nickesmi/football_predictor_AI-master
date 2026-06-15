from curl_cffi import requests

url = "https://api.sofascore.com/api/v1/sport/tennis/scheduled-events/2026-06-15"
headers = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

try:
    r = requests.get(url, headers=headers, impersonate="chrome120", timeout=10)
    print(f"Status: {r.status_code}")
    data = r.json()
    events = data.get("events", [])
    print(f"Tennis events found: {len(events)}")
    if events:
        print(f"Sample: {events[0].get('homeTeam', {}).get('name')} vs {events[0].get('awayTeam', {}).get('name')}")
except Exception as e:
    print(f"Error: {e}")
