"""
Logo Service Module.

Provides business logic for team and tournament logo resolution, image proxying
with Cloudflare bypass, local logo caching and serving, quality auditing, and
rendering verification.
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List
from fastapi import BackgroundTasks, Response
from fastapi.responses import FileResponse

logger = logging.getLogger("football_predictor")


def resolve_team_logo(team_id: str, team_name: str, fallback_url: str) -> str:
    """
    Check the team logo registry in the database for the highest quality logo.

    Args:
        team_id: The unique identifier for the team.
        team_name: The name of the team.
        fallback_url: The URL to return if no high-quality logo is found in registry.

    Returns:
        The local path or URL of the best quality logo, or fallback_url.
    """
    from src.db.database import get_db

    try:
        conn = get_db()
        row = conn.execute(
            "SELECT local_path, logo_url, quality_grade FROM team_logo_registry WHERE team_id = ? OR team_name = ?",
            (team_id, team_name),
        ).fetchone()
        if row:
            grade = row["quality_grade"]
            if grade in ("GOOD", "EXCELLENT") and row["local_path"]:
                return str(row["local_path"])
            elif grade in ("GOOD", "EXCELLENT") and row["logo_url"]:
                return str(row["logo_url"])
    except Exception as e:
        logger.error(f"Failed to resolve logo for {team_name}: {e}")
    return fallback_url


def proxy_image_url(url: str) -> Response:
    """
    Fetch an image from an external provider (such as SofaScore) by impersonating a browser.

    Args:
        url: The external image URL to proxy.

    Returns:
        A FastAPI Response with image/png content on success, or 404 status code on failure.
    """
    from curl_cffi import requests

    try:
        resp = requests.get(
            url,
            impersonate="chrome110",
            timeout=10,
            headers={
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": "https://www.sofascore.com/",
                "Origin": "https://www.sofascore.com",
            },
        )
        if resp.status_code == 200:
            return Response(content=resp.content, media_type="image/png")
        else:
            logger.warning(f"SofaScore proxy failed for {url} with status {resp.status_code}")
            return Response(status_code=404)
    except Exception as e:
        logger.error(f"Failed to proxy image {url}: {e}")
        return Response(status_code=404)


def serve_local_logo_file(filename: str) -> Response:
    """
    Serve a cached local logo image file from disk.

    Args:
        filename: The filename of the logo within data/logos/.

    Returns:
        A FileResponse if the file exists, otherwise a 404 Response.
    """
    path = Path("data/logos") / filename
    if path.exists():
        return FileResponse(path)
    return Response(status_code=404)


def audit_team_logos(
    date_str: str, background_tasks: BackgroundTasks, get_fixtures_fn: Callable[..., List[Dict[str, Any]]]
) -> Dict[str, Any]:
    """
    Diagnose logo resolutions for teams playing on a given date and auto-upgrade if quality is poor.

    Args:
        date_str: The target date string (YYYY-MM-DD).
        background_tasks: FastAPI BackgroundTasks instance to pass to fixture retriever.
        get_fixtures_fn: Function to retrieve fixtures for the specified date.

    Returns:
        A dictionary summarizing the audit results and upgrades performed.
    """
    from src.db.database import get_db
    from src.utils.logo_evaluator import find_best_logo

    fixtures = get_fixtures_fn(date_str, background_tasks)
    if not fixtures:
        return {"status": "no fixtures"}

    audit_results: List[Dict[str, Any]] = []
    conn = get_db()

    teams_to_check: Dict[str, str] = {}
    for f in fixtures:
        teams_to_check[str(f["home_team"]["id"])] = f["home_team"]["name"]
        teams_to_check[str(f["away_team"]["id"])] = f["away_team"]["name"]

    upgrades_done = 0

    for tid, tname in teams_to_check.items():
        row = conn.execute("SELECT * FROM team_logo_registry WHERE team_id = ?", (tid,)).fetchone()

        needs_upgrade = False
        if not row:
            needs_upgrade = True
        elif row["quality_grade"] in ("POOR", "FAIR"):
            needs_upgrade = True

        if needs_upgrade:
            best = find_best_logo(tid, tname, sofa_id=tid, api_id=tid)
            conn.execute(
                """
                INSERT INTO team_logo_registry 
                (team_id, team_name, provider, logo_url, local_path, etag, width, height, file_size, sharpness_score, quality_score, quality_grade, logo_source_rank, upgrade_reason, recheck_after_days)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(team_id) DO UPDATE SET
                provider=excluded.provider, logo_url=excluded.logo_url, local_path=excluded.local_path, etag=excluded.etag, width=excluded.width, height=excluded.height, file_size=excluded.file_size, sharpness_score=excluded.sharpness_score, quality_score=excluded.quality_score, quality_grade=excluded.quality_grade, logo_source_rank=excluded.logo_source_rank, upgrade_reason=excluded.upgrade_reason, recheck_after_days=excluded.recheck_after_days, last_downloaded=CURRENT_TIMESTAMP
            """,
                (
                    tid,
                    tname,
                    best["provider"],
                    best["logo_url"],
                    best["local_path"],
                    best["etag"],
                    best["width"],
                    best["height"],
                    best["file_size"],
                    best["sharpness_score"],
                    best["quality_score"],
                    best["quality_grade"],
                    best["logo_source_rank"],
                    best["upgrade_reason"],
                    best["recheck_after_days"],
                ),
            )
            conn.commit()
            upgrades_done += 1
            audit_results.append(best)
        else:
            audit_results.append(dict(row))

    return {
        "status": "completed",
        "teams_audited": len(teams_to_check),
        "upgrades_performed": upgrades_done,
        "results": audit_results,
    }


def get_logo_health_summary() -> Dict[str, Any]:
    """
    Aggregate quality grade counts across the entire team logo registry.

    Returns:
        A dictionary containing total count and breakdown by quality grade.
    """
    from src.db.database import get_db

    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM team_logo_registry").fetchone()["c"]
    excellent = conn.execute(
        "SELECT COUNT(*) as c FROM team_logo_registry WHERE quality_grade='EXCELLENT'"
    ).fetchone()["c"]
    good = conn.execute("SELECT COUNT(*) as c FROM team_logo_registry WHERE quality_grade='GOOD'").fetchone()["c"]
    fair = conn.execute("SELECT COUNT(*) as c FROM team_logo_registry WHERE quality_grade='FAIR'").fetchone()["c"]
    poor = conn.execute("SELECT COUNT(*) as c FROM team_logo_registry WHERE quality_grade='POOR'").fetchone()["c"]
    return {"total_teams": total, "excellent": excellent, "good": good, "fair": fair, "poor": poor}


def get_logo_upgrades_summary() -> Dict[str, Any]:
    """
    Retrieve statistics on logo upgrades performed today and within the last 7 days.

    Returns:
        A dictionary summarizing recent upgrade activity and remaining poor quality logos.
    """
    from src.db.database import get_db

    conn = get_db()
    upgraded_today = conn.execute(
        "SELECT COUNT(*) as c FROM team_logo_registry WHERE date(last_downloaded) = date('now') AND upgrade_reason != 'Initial Check'"
    ).fetchone()["c"]
    upgraded_this_week = conn.execute(
        "SELECT COUNT(*) as c FROM team_logo_registry WHERE date(last_downloaded) >= date('now', '-7 days') AND upgrade_reason != 'Initial Check'"
    ).fetchone()["c"]
    poor_remaining = conn.execute(
        "SELECT COUNT(*) as c FROM team_logo_registry WHERE quality_grade='POOR'"
    ).fetchone()["c"]

    return {
        "upgraded_today": upgraded_today,
        "upgraded_this_week": upgraded_this_week,
        "poor_remaining": poor_remaining,
        "largest_improvement": {"team": "Currently not tracked differentially", "old": "N/A", "new": "N/A"},
    }


def audit_logo_rendering(render_w: int = 64, render_h: int = 64, dpr: float = 2.0) -> Dict[str, Any]:
    """
    Verify whether registered team logos suffer from upscaling at specified target render resolutions.

    Args:
        render_w: Target rendering width in logical CSS pixels.
        render_h: Target rendering height in logical CSS pixels.
        dpr: Target device pixel ratio (e.g. 2.0 for Retina displays).

    Returns:
        A dictionary summarizing checked teams and details of any upscaled logos.
    """
    from src.db.database import get_db

    conn = get_db()
    rows = conn.execute("SELECT team_name, width, height, quality_grade FROM team_logo_registry").fetchall()

    issues: List[Dict[str, Any]] = []
    for r in rows:
        w = r["width"] or 1
        h = r["height"] or 1
        effective_scale = (render_w * dpr) / w
        issue = "UPSCALED" if effective_scale > 1.0 else "OK"
        if issue == "UPSCALED":
            issues.append(
                {
                    "team": r["team_name"],
                    "source_resolution": f"{w}x{h}",
                    "rendered_resolution": f"{int(render_w * dpr)}x{int(render_h * dpr)}",
                    "dpr": dpr,
                    "effective_scale": round(effective_scale, 2),
                    "issue": issue,
                }
            )

    return {
        "status": "completed",
        "total_teams_checked": len(rows),
        "upscaled_issues": len(issues),
        "details": issues,
    }
