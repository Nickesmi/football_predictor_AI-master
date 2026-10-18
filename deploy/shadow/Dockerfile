# Shadow-experiment runtime (M3.4). NOT built or deployed by any script; owner approval required.
# Build:  docker build -f deploy/shadow/Dockerfile -t football-shadow:m3.4 .
# The image runs one shadow cycle per invocation; scheduling stays outside (systemd timer on the host).
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production
RUN useradd --create-home --uid 10001 shadow
WORKDIR /app
COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt
COPY --chown=shadow:shadow . .
USER shadow
# /shadow holds the isolated shadow DB, its workspace/cache, the shared ledger and logs (a mounted volume)
VOLUME ["/shadow"]
ENTRYPOINT ["python", "scripts/shadow_cycle.py", "--db", "/shadow/engine_shadow.db", "--ledger", "/shadow/api_football_ledger.db", "--profile", "medium", "--max-requests-per-cycle", "20"]
CMD ["--budget", "0"]
