import io
import os
import hashlib
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from PIL import Image, ImageFilter, ImageStat

logger = logging.getLogger("football_predictor")

# Ensure logos directory exists
LOGOS_DIR = Path("data/logos")
LOGOS_DIR.mkdir(parents=True, exist_ok=True)

# Ranking of providers
PROVIDER_RANK = {
    "Local Cache": 1,
    "SofaScore": 2,
    "Wikimedia": 3,
    "Club Website": 4,
    "API-Football": 5,
}

def _fetch_image_bytes(url: str) -> Optional[bytes]:
    """Fetch image bytes with browser impersonation to bypass 403s."""
    from curl_cffi import requests
    try:
        resp = requests.get(
            url,
            impersonate="chrome",
            timeout=10,
            headers={"Referer": "https://www.sofascore.com/"}
        )
        if resp.status_code == 200 and len(resp.content) > 0:
            return resp.content
    except Exception as e:
        logger.debug(f"Failed to fetch image from {url}: {e}")
    return None

def compute_sharpness(img: Image.Image) -> float:
    """Compute a sharpness score using Laplacian variance."""
    # Convert to grayscale
    gray = img.convert('L')
    # Apply edge enhancement / find edges
    edges = gray.filter(ImageFilter.FIND_EDGES)
    # Variance of the edges is a common proxy for sharpness/blur detection
    stat = ImageStat.Stat(edges)
    variance = stat.var[0] if stat.var else 0.0
    return round(variance, 2)

def evaluate_logo(image_bytes: bytes) -> Dict[str, Any]:
    """Evaluate image dimensions, size, and sharpness."""
    if not image_bytes:
        return {"width": 0, "height": 0, "file_size": 0, "sharpness_score": 0.0}
    
    file_size = len(image_bytes)
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size
            sharpness = compute_sharpness(img)
            return {
                "width": width,
                "height": height,
                "file_size": file_size,
                "sharpness_score": sharpness
            }
    except Exception as e:
        logger.debug(f"Pillow failed to read image: {e}")
        return {"width": 0, "height": 0, "file_size": file_size, "sharpness_score": 0.0}

def calculate_quality_grade(w: int, h: int, size: int, sharpness: float) -> Tuple[str, int]:
    """
    Returns (grade, recheck_after_days).
    Retina-aware grading. Penalizes upscaled/blurry images.
    """
    if w == 0 or h == 0:
        return "POOR", 7
        
    min_dim = min(w, h)
    
    # If it's technically large but very blurry (low sharpness variance), downgrade it.
    # Typical sharp logos have edge variance > 500. Very blurry < 100.
    is_blurry = sharpness < 150.0 and min_dim >= 64
    
    if min_dim < 64:
        return "POOR", 7
    elif min_dim < 128 or is_blurry:
        return "FAIR", 30
    elif min_dim < 256:
        return "GOOD", 90
    else:
        return "EXCELLENT", 180

def cache_logo_locally(team_id: str, image_bytes: bytes) -> Tuple[str, str]:
    """Saves to data/logos/ and returns (local_path, etag)."""
    filename = f"team_{team_id}.png"
    filepath = LOGOS_DIR / filename
    
    try:
        # Save explicitly as PNG to standardize
        with Image.open(io.BytesIO(image_bytes)) as img:
            # Convert to RGBA if not already to preserve transparency
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            img.save(filepath, format="PNG")
            
        etag = hashlib.md5(filepath.read_bytes()).hexdigest()
        return f"/api/image/local/{filename}", etag
    except Exception as e:
        logger.error(f"Failed to cache logo locally for {team_id}: {e}")
        return "", ""

def get_wikimedia_url(team_name: str) -> Optional[str]:
    """Attempt to find a wikipedia logo via Wikipedia API (basic heuristic)."""
    import urllib.parse
    from curl_cffi import requests
    try:
        query = urllib.parse.quote(f"{team_name} logo")
        url = f"https://en.wikipedia.org/w/api.php?action=query&titles={query}&prop=pageimages&format=json&pithumbsize=512"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            for page_id, page_data in pages.items():
                if "thumbnail" in page_data:
                    return page_data["thumbnail"]["source"]
    except Exception:
        pass
    return None

def find_best_logo(team_id: str, team_name: str, sofa_id: str = None, api_id: str = None) -> Dict[str, Any]:
    """
    Attempts to find the highest-quality logo, respecting source priorities to save quota.
    """
    candidates = []
    
    if sofa_id:
        candidates.append(("SofaScore", f"https://api.sofascore.app/api/v1/team/{sofa_id}/image"))
        
    candidates.append(("Wikimedia", get_wikimedia_url(team_name)))
    
    if api_id:
        # API-Football (last resort)
        candidates.append(("API-Football", f"https://media.api-sports.io/football/teams/{api_id}.png"))
        
    best_eval = {"width": 0, "height": 0, "file_size": 0, "sharpness_score": 0.0}
    best_grade = "POOR"
    best_url = None
    best_provider = None
    best_bytes = None
    best_recheck = 7
    
    for provider, url in candidates:
        if not url:
            continue
            
        img_bytes = _fetch_image_bytes(url)
        if not img_bytes:
            continue
            
        ev = evaluate_logo(img_bytes)
        grade, recheck = calculate_quality_grade(ev["width"], ev["height"], ev["file_size"], ev["sharpness_score"])
        
        # We want to maximize the rank. POOR=0, FAIR=1, GOOD=2, EXCELLENT=3
        grade_rank = {"POOR": 0, "FAIR": 1, "GOOD": 2, "EXCELLENT": 3}
        
        if grade_rank[grade] > grade_rank.get(best_grade, -1) or (grade_rank[grade] == grade_rank.get(best_grade, -1) and ev["width"] > best_eval["width"]):
            best_eval = ev
            best_grade = grade
            best_url = url
            best_provider = provider
            best_bytes = img_bytes
            best_recheck = recheck
            
            # If we found an EXCELLENT logo, stop searching!
            if grade == "EXCELLENT":
                break

    # Cache locally if we got something
    local_path = ""
    etag = ""
    if best_bytes:
        local_path, etag = cache_logo_locally(team_id, best_bytes)
        
    return {
        "team_id": str(team_id),
        "team_name": team_name,
        "provider": best_provider or "Unknown",
        "logo_url": best_url or "",
        "local_path": local_path,
        "etag": etag,
        "width": best_eval["width"],
        "height": best_eval["height"],
        "file_size": best_eval["file_size"],
        "sharpness_score": best_eval["sharpness_score"],
        "quality_score": best_eval["sharpness_score"] + min(best_eval["width"], 256),  # simple arbitrary composite
        "quality_grade": best_grade,
        "logo_source_rank": PROVIDER_RANK.get(best_provider, 99),
        "upgrade_reason": f"Upgraded to {best_provider} ({best_grade})" if best_provider else "Initial Check",
        "recheck_after_days": best_recheck
    }
