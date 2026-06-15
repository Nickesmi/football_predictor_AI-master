from sports_skills import tennis
import json

res = tennis.get_scoreboard(tour="atp")
for t in res['data']['tournaments']:
    for d in t.get('draws', []):
        matches = d.get('matches', [])
        print(f"Tournament: {t['name']} - Draw: {d['name']} - Matches: {len(matches)}")
