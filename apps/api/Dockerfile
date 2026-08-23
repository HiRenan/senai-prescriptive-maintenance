FROM scratch AS context-audit

COPY . /context

FROM ghcr.io/astral-sh/uv:0.9.4@sha256:c4089b0085cf4d38e38d5cdaa5e57752c1878a6f41f2e3a3a234dc5f23942cb4 AS uv

FROM python:3.13-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1 AS builder

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/README.md ./apps/api/

RUN uv sync --frozen --no-dev --no-install-workspace

COPY apps/api/src ./apps/api/src

RUN uv sync --frozen --no-dev --no-editable

FROM builder AS builder-audit

RUN /opt/venv/bin/python -c "from importlib.util import find_spec; from pathlib import Path; workspace = Path('/workspace'); venv = Path('/opt/venv'); forbidden_parts = {'tests', 'openapi', '__pycache__', '.git', '.cache', '.pytest_cache', '.ruff_cache', '.mypy_cache'}; repo_bad = [path for path in workspace.rglob('*') if any(part.lower() in forbidden_parts for part in path.parts) or path.suffix.lower() in {'.pyc', '.pyo', '.pdf', '.csv'} or path.name.lower() == '.env' or path.name.lower().startswith('.env.')]; venv_cache = [path for path in venv.rglob('*') if path.name.lower() == '__pycache__' or path.suffix.lower() in {'.pyc', '.pyo'}]; dev_modules = [name for name in ('coverage', 'pre_commit', 'poethepoet', 'pyright', 'pytest', 'ruff') if find_spec(name) is not None]; failed = bool(repo_bad or venv_cache or dev_modules); print(f'API builder audit: repository_residues={len(repo_bad)} cache_artifacts={len(venv_cache)} development_modules={len(dev_modules)}'); raise SystemExit(failed)"

FROM python:3.13-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1 AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder --chown=65532:65532 /opt/venv /opt/venv

WORKDIR /app

USER 65532:65532

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "from urllib.request import urlopen; response = urlopen('http://127.0.0.1:8000/health/ready', timeout=2); body = response.read(32); content_type = response.headers.get_content_type(); response.close(); raise SystemExit(0 if response.status == 200 and content_type == 'application/json' and body == b'{\"status\":\"ready\"}' else 1)"]

CMD ["python", "-m", "uvicorn", "prescriptive_maintenance.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
