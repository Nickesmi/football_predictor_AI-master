import requests as req
from curl_cffi import requests
url = "https://api.sofascore.com/api/v1/sport/football/scheduled-events/2026-06-12"
headers = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}
browsers = ["chrome120", "chrome124", "safari17_0", "edge122", "chrome116", "safari15_3"]
for b in browsers:
    try:
        r = requests.get(url, headers=headers, impersonate=b, timeout=5)
        print(f"{b}: {r.status_code}")
    except Exception as e:
        print(f"{b}: error {e}")
