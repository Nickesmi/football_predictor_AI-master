#!/usr/bin/env python3
"""Browser regression for cross-competition history (2026-11-03). Run against an ISOLATED demo stack only.

    python3 scripts/browser_regression_xcomp.py --url http://127.0.0.1:5183 --day 2026-11-03 --shots <dir>

Needs Python Playwright with Chromium. Every request that does not go to 127.0.0.1/localhost is aborted in the
browser, so the page cannot reach any provider. Checks:
  1. Feyenoord vs Inter (Champions League) shows a prediction, with the domestic-history notes.
  2. A fixture without verified history shows "Prediction unavailable" with a precise reason, no probabilities.
  3. 24 concurrent analysis requests (through the page's own origin) all succeed and agree with a repeat.
  4. No browser request left localhost.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

LOCAL = {"127.0.0.1", "localhost"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--day", required=True)
    p.add_argument("--shots", required=True, type=Path)
    a = p.parse_args()
    a.shots.mkdir(parents=True, exist_ok=True)
    results, blocked = {}, []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})

        def guard(route):
            host = urlparse(route.request.url).hostname or ""
            if host in LOCAL:
                return route.continue_()
            blocked.append(route.request.url)
            return route.abort()

        page.route("**/*", guard)
        page.goto(a.url, wait_until="domcontentloaded")
        page.locator('input[type="date"]').fill(a.day)
        page.get_by_text("Feyenoord", exact=True).first.wait_for(timeout=30000)
        fixtures = page.evaluate(f"fetch('/api/fixtures/{a.day}').then(r => r.json())")
        fixtures = fixtures.get("fixtures", fixtures) if isinstance(fixtures, dict) else fixtures

        # 1. Feyenoord vs Inter in the UI
        fey_fx = next(f for f in fixtures if f["home_team"]["name"] == "Feyenoord" and f["away_team"]["name"] == "Inter")
        with page.expect_response(lambda r: f"/api/analysis/match/{fey_fx['id']}?" in r.url, timeout=60000) as resp:
            page.get_by_text("Feyenoord", exact=True).first.click()
        fey = resp.value.json()
        page.get_by_text("uses its domestic 'Eredivisie' history", exact=False).first.wait_for(timeout=30000)
        page.screenshot(path=str(a.shots / "feyenoord_inter.png"), full_page=True)
        body = page.inner_text("body")
        results["feyenoord_inter"] = {
            "prediction_available": fey.get("prediction_available"),
            "unavailable_panel_shown": "Prediction unavailable" in body,
            "home_source": fey["identity"]["home_history"]["source"],
            "home_record": fey["identity"]["home_history"]["record"],
            "away_source": fey["identity"]["away_history"]["source"],
            "away_record": fey["identity"]["away_history"]["record"],
            "notes": fey.get("missing_inputs"),
            "notes_visible": all(n in body for n in fey.get("missing_inputs", [])),
        }

        # 3. concurrent analysis requests through the page origin (identity params as the frontend sends them)
        def q(f):
            lg = f.get("league") or {}
            params = {"home": f["home_team"]["name"], "away": f["away_team"]["name"], "league": lg.get("name", ""),
                      "provider": f.get("source") or f.get("provider"),
                      "league_id": f.get("league_id_apifootball") or lg.get("id"), "country": lg.get("country", ""),
                      "home_id": f["home_team"].get("id"), "away_id": f["away_team"].get("id"),
                      "kickoff_utc": f.get("kickoff_utc")}
            from urllib.parse import urlencode
            return f"/api/analysis/match/{f['id']}?" + urlencode({k: v for k, v in params.items() if v})

        sample = [f for f in fixtures if f.get("source") == "api_football"][:24]
        js = "urls => Promise.all(urls.map(u => fetch(u).then(async r => ({s: r.status, j: await r.json()}))))"
        first = page.evaluate(js, [q(f) for f in sample])
        again = page.evaluate(js, [q(f) for f in sample])
        key = lambda x: (x["s"], x["j"].get("prediction_available"), json.dumps(x["j"].get("missing_inputs")))
        results["concurrent"] = {"requests": len(first), "all_200": all(x["s"] == 200 for x in first + again),
                                 "repeat_identical": [key(x) for x in first] == [key(x) for x in again],
                                 "available": sum(1 for x in first if x["j"].get("prediction_available") is not False)}

        # 2. an unavailable fixture: the first one in the sample (or the day) that the backend refuses
        target = next((f for f, x in zip(sample, first) if x["j"].get("prediction_available") is False), None)
        if target is None:
            for f in fixtures:
                if f.get("source") != "api_football":
                    continue
                j = page.evaluate("u => fetch(u).then(r => r.json())", q(f))
                if j.get("prediction_available") is False:
                    target = f
                    break
        if target is not None:
            page.locator('input[type="date"]').fill(a.day)
            row = page.get_by_text(target["home_team"]["name"], exact=True).first
            row.scroll_into_view_if_needed()
            row.click()
            page.get_by_text("Prediction unavailable", exact=False).first.wait_for(timeout=30000)
            page.screenshot(path=str(a.shots / "unavailable.png"), full_page=True)
            body = page.inner_text("body")
            results["unavailable"] = {"fixture": f"{target['home_team']['name']} v {target['away_team']['name']} "
                                                 f"({target['league']['name']})",
                                      "panel_shown": "Prediction unavailable" in body,
                                      "reason_visible": "has NO real data" in body or "already includes results" in body,
                                      "no_percentages_in_panel": "Home Win" not in body}
        browser.close()
    results["blocked_non_local_requests"] = len(blocked)
    results["blocked_hosts"] = sorted({urlparse(u).hostname for u in blocked})
    print(json.dumps(results, indent=1, ensure_ascii=False))
    ok = (results["feyenoord_inter"]["prediction_available"] is True
          and not results["feyenoord_inter"]["unavailable_panel_shown"]
          and results["feyenoord_inter"]["home_source"] == "domestic_league"
          and results["feyenoord_inter"]["notes_visible"]
          and results["concurrent"]["all_200"] and results["concurrent"]["repeat_identical"]
          and results.get("unavailable", {}).get("panel_shown") and results["unavailable"]["reason_visible"])
    print("BROWSER REGRESSION", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
