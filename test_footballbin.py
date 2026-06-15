import requests
import json

payload = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
        "name": "get_match_predictions",
        "arguments": {
            "league": "premier_league",
            "matchweek": 10
        }
    }
}
r = requests.post("https://ru7m5svay1.execute-api.eu-central-1.amazonaws.com/prod/mcp", json=payload)
print(json.dumps(r.json(), indent=2))
