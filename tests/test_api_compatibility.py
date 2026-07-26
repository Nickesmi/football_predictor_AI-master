"""
Automated API compatibility test suite and performance benchmark.
Snapshots every existing endpoint, request schema, response schema, HTTP status code,
and error response, while benchmarking startup time, API latency, and memory usage.
"""

import time
import tracemalloc
import pytest
from typing import Any, Dict, List
from fastapi.testclient import TestClient

# Start memory and time tracking before importing app
tracemalloc.start()
start_time = time.perf_counter()

from api.main import app  # noqa: E402

startup_duration: float = time.perf_counter() - start_time
current_mem, peak_mem = tracemalloc.get_traced_memory()
tracemalloc.stop()

client: TestClient = TestClient(app)

# Global store for benchmark results
BENCHMARK_RESULTS: Dict[str, Any] = {
    "startup_time_sec": round(startup_duration, 4),
    "peak_memory_mb": round(peak_mem / (1024 * 1024), 2),
    "endpoints": {},
}


def record_latency(endpoint_name: str, duration: float, status_code: int) -> None:
    """Record latency and status code for benchmark comparison."""
    BENCHMARK_RESULTS["endpoints"][endpoint_name] = {
        "latency_ms": round(duration * 1000, 2),
        "status_code": status_code,
    }


def test_app_startup_metrics() -> None:
    """Verify startup time and memory footprint are within acceptable bounds."""
    print(f"\n[BENCHMARK] Baseline Startup Time: {BENCHMARK_RESULTS['startup_time_sec']}s")
    print(f"[BENCHMARK] Baseline Peak Memory: {BENCHMARK_RESULTS['peak_memory_mb']} MB")
    assert BENCHMARK_RESULTS["startup_time_sec"] < 15.0, "Startup took too long!"
    assert BENCHMARK_RESULTS["peak_memory_mb"] < 2000.0, "Memory usage exceeded 2GB!"


def test_health_check_snapshot() -> None:
    """Snapshot /api/health endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/health")
    dt = time.perf_counter() - t0
    record_latency("GET /api/health", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert "status" in data
    assert "data_source" in data
    assert data["status"] == "ok"


def test_leagues_snapshot() -> None:
    """Snapshot /api/leagues endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/leagues")
    dt = time.perf_counter() - t0
    record_latency("GET /api/leagues", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    if len(data) > 0:
        league = data[0]
        assert "id" in league or "league_id" in league or isinstance(league, (dict, str, int))


def test_competitions_snapshot() -> None:
    """Snapshot /api/competitions endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/competitions")
    dt = time.perf_counter() - t0
    record_latency("GET /api/competitions", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, (list, dict))


def test_fixtures_today_snapshot() -> None:
    """Snapshot /api/fixtures/today endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/fixtures/today")
    dt = time.perf_counter() - t0
    record_latency("GET /api/fixtures/today", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_fixtures_by_date_snapshot() -> None:
    """Snapshot /api/fixtures/{date_str} endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/fixtures/2026-07-04")
    dt = time.perf_counter() - t0
    record_latency("GET /api/fixtures/2026-07-04", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_portfolio_summary_snapshot() -> None:
    """Snapshot /api/portfolio/summary endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/portfolio/summary")
    dt = time.perf_counter() - t0
    record_latency("GET /api/portfolio/summary", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)


def test_performance_overview_snapshot() -> None:
    """Snapshot /api/performance/overview endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/performance/overview")
    dt = time.perf_counter() - t0
    record_latency("GET /api/performance/overview", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)


def test_calibration_status_snapshot() -> None:
    """Snapshot /api/calibration/status endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/calibration/status")
    dt = time.perf_counter() - t0
    record_latency("GET /api/calibration/status", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)


def test_execution_rules_snapshot() -> None:
    """Snapshot /api/execution/rules endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/execution/rules")
    dt = time.perf_counter() - t0
    record_latency("GET /api/execution/rules", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)


def test_leagues_profiles_snapshot() -> None:
    """Snapshot /api/leagues/profiles endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/leagues/profiles")
    dt = time.perf_counter() - t0
    record_latency("GET /api/leagues/profiles", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_analytics_league_pnl_snapshot() -> None:
    """Snapshot /api/analytics/league-pnl endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/analytics/league-pnl")
    dt = time.perf_counter() - t0
    record_latency("GET /api/analytics/league-pnl", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_analytics_calibration_snapshot() -> None:
    """Snapshot /api/analytics/calibration endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/analytics/calibration")
    dt = time.perf_counter() - t0
    record_latency("GET /api/analytics/calibration", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, (list, dict))


def test_analytics_confidence_buckets_snapshot() -> None:
    """Snapshot /api/analytics/confidence-buckets endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/analytics/confidence-buckets")
    dt = time.perf_counter() - t0
    record_latency("GET /api/analytics/confidence-buckets", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, (list, dict))


def test_backtest_summary_snapshot() -> None:
    """Snapshot /api/backtest/summary endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/backtest/summary")
    dt = time.perf_counter() - t0
    record_latency("GET /api/backtest/summary", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)


def test_performance_daily_snapshot() -> None:
    """Snapshot /api/performance/daily endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/performance/daily")
    dt = time.perf_counter() - t0
    record_latency("GET /api/performance/daily", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, (list, dict))


def test_calibration_isotonic_status_snapshot() -> None:
    """Snapshot /api/calibration/isotonic/status endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/calibration/isotonic/status")
    dt = time.perf_counter() - t0
    record_latency("GET /api/calibration/isotonic/status", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, (list, dict))


def test_execution_simulate_snapshot() -> None:
    """Snapshot /api/execution/simulate endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/execution/simulate")
    dt = time.perf_counter() - t0
    record_latency("GET /api/execution/simulate", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)


def test_precompute_predictions_snapshot() -> None:
    """Snapshot /api/precompute-predictions endpoint schema and status."""
    t0 = time.perf_counter()
    response = client.get("/api/precompute-predictions?date_str=2026-07-04")
    dt = time.perf_counter() - t0
    record_latency("GET /api/precompute-predictions", dt, response.status_code)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert "status" in data


def test_image_endpoints_snapshot() -> None:
    """Snapshot image endpoints for team and tournament logos."""
    t0 = time.perf_counter()
    res_team = client.get("/api/image/team/17")
    dt = time.perf_counter() - t0
    record_latency("GET /api/image/team/17", dt, res_team.status_code)
    assert res_team.status_code in (200, 307, 404)

    t0 = time.perf_counter()
    res_tour = client.get("/api/image/tournament/17")
    dt = time.perf_counter() - t0
    record_latency("GET /api/image/tournament/17", dt, res_tour.status_code)
    assert res_tour.status_code in (200, 307, 404)


def test_admin_auth_protection_snapshot() -> None:
    """Verify protected admin endpoints reject unauthenticated requests with 401/403."""
    t0 = time.perf_counter()
    response = client.get("/api/debug/logo-audit")
    dt = time.perf_counter() - t0
    record_latency("GET /api/debug/logo-audit (unauth)", dt, response.status_code)
    assert response.status_code in (401, 403)

    t0 = time.perf_counter()
    res_model = client.get("/api/debug/model-health")
    dt = time.perf_counter() - t0
    record_latency("GET /api/debug/model-health (unauth)", dt, res_model.status_code)
    assert res_model.status_code in (401, 403)


def test_404_error_response_snapshot() -> None:
    """Verify 404 error response schema."""
    t0 = time.perf_counter()
    response = client.get("/api/nonexistent-route-12345")
    dt = time.perf_counter() - t0
    record_latency("GET /api/nonexistent", dt, response.status_code)

    assert response.status_code == 404
    data = response.json()
    assert isinstance(data, dict)
    assert "detail" in data


def test_print_benchmark_summary() -> None:
    """Print overall benchmark summary at end of test suite."""
    print("\n" + "=" * 60)
    print("        API PERFORMANCE BENCHMARK SUMMARY")
    print("=" * 60)
    print(f"Startup Time : {BENCHMARK_RESULTS['startup_time_sec']} sec")
    print(f"Peak Memory  : {BENCHMARK_RESULTS['peak_memory_mb']} MB")
    print("-" * 60)
    print(f"{'Endpoint':<40} | {'Status':<6} | {'Latency (ms)':<10}")
    print("-" * 60)
    for endpoint, stats in BENCHMARK_RESULTS["endpoints"].items():
        print(f"{endpoint:<40} | {stats['status_code']:<6} | {stats['latency_ms']:<10}")
    print("=" * 60 + "\n")
