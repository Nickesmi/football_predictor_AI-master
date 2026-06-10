from curl_cffi import requests

url = "https://api.sofascore.com/api/v1/sport/football/scheduled-events/2026-06-03"
headers = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

try:
    resp = requests.get(url, headers=headers, impersonate="chrome", timeout=15)
    events = resp.json().get("events", [])
    print(f"Total events: {len(events)}")
    for ev in events[:20]:  # print first 20
        tournament = ev.get("tournament", {})
        ut = tournament.get("uniqueTournament", {})
        print(f"Match: {ev['homeTeam']['name']} vs {ev['awayTeam']['name']}")
        print(f"  League: {tournament.get('name')} | UniqueTournament: {ut.get('name')} (ID: {ut.get('id')})")
except Exception as e:
    print("Error:", e)
