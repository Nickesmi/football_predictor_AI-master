"""
Images & Logos Router.

Handles proxying external team/tournament images, serving local logo files,
and debugging logo resolutions and quality upgrades.
"""

from typing import Any, Dict
from fastapi import APIRouter, BackgroundTasks, Depends, Response
from api.dependencies import verify_admin_key
from api.services import logo_service

router = APIRouter(tags=["Images & Logos"])


@router.get("/api/image/team/{team_id}")
def get_team_image(team_id: str) -> Response:
    """Proxy team logo image from external provider to bypass restrictions."""
    url = f"https://api.sofascore.com/api/v1/team/{team_id}/image"
    return logo_service.proxy_image_url(url)


@router.get("/api/image/tournament/{tour_id}")
def get_tournament_image(tour_id: str) -> Response:
    """Proxy tournament logo image from external provider."""
    url = f"https://api.sofascore.com/api/v1/unique-tournament/{tour_id}/image"
    return logo_service.proxy_image_url(url)


@router.get("/api/image/local/{filename}")
def serve_local_image(filename: str) -> Response:
    """Serve a locally cached team logo image file."""
    return logo_service.serve_local_logo_file(filename)


@router.get("/api/debug/logo-audit", dependencies=[Depends(verify_admin_key)])
def logo_audit(date: str, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Diagnose logo resolutions for fixtures on a date and auto-upgrade if poor."""
    from api.main import get_fixtures_by_date

    return logo_service.audit_team_logos(date, background_tasks, get_fixtures_by_date)


@router.get("/api/debug/logo-health", dependencies=[Depends(verify_admin_key)])
def logo_health() -> Dict[str, Any]:
    """Aggregate quality grade counts across the team logo registry."""
    return logo_service.get_logo_health_summary()


@router.get("/api/debug/logo-upgrades", dependencies=[Depends(verify_admin_key)])
def logo_upgrades() -> Dict[str, Any]:
    """Return statistics on recent logo upgrades and remaining poor quality logos."""
    return logo_service.get_logo_upgrades_summary()


@router.get("/api/debug/logo-render-audit", dependencies=[Depends(verify_admin_key)])
def logo_render_audit(render_w: int = 64, render_h: int = 64, dpr: float = 2.0) -> Dict[str, Any]:
    """Check if registered team logos suffer from upscaling at target render dimensions."""
    return logo_service.audit_logo_rendering(render_w=render_w, render_h=render_h, dpr=dpr)
