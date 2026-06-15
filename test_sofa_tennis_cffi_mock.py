from curl_cffi import requests

date_str = "2026-06-15"
url = f"https://api.sofascore.com/api/v1/sport/tennis/scheduled-events/{date_str}"
headers = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

try:
    r = requests.get(url, headers=headers, impersonate="chrome120", timeout=10)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        events = data.get("events", [])
        print(f"Tennis events found: {len(events)}")
        if events:
            ev = events[0]
            print(f"Sample: {ev.get('homeTeam', {}).get('name')} vs {ev.get('awayTeam', {}).get('name')}")
            print(f"ID: {ev.get('id')}, Status: {ev.get('status', {}).get('type')}, Tourney: {ev.get('tournament', {}).get('name')}")
except Exception as e:
    print(f"Error: {e}")
