FROM python:3.12-slim-bookworm
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src MPLBACKEND=Agg MPLCONFIGDIR=/tmp/matplotlib \
    SAT_PROJECT_ROOT=/app SAT_STATE_DIR=/var/lib/satellite-coverage \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /app
COPY requirements-api.lock ./
RUN pip install --default-timeout=30 --no-cache-dir --index-url ${PIP_INDEX_URL} -r requirements-api.lock \
    && python -c "import pulp; assert pulp.PULP_CBC_CMD().available(), 'CBC unavailable'" \
    && useradd --uid 10001 --create-home satellite \
    && mkdir -p /var/lib/satellite-coverage \
    && chown satellite:satellite /var/lib/satellite-coverage
# 白名单复制产品必需模块，不带入 GUI、ATK、实验或主工作树环境。
COPY src/coverage_api/ src/coverage_api/
COPY src/siriagent_skill/ src/siriagent_skill/
COPY src/coverage_analysis.py src/sensor_geometry.py src/region_grid.py src/regional_opportunities.py src/regional_metrics.py src/regional_selection.py src/satellite_loader.py src/siriagent_catalog.py src/
COPY data/geo/*.json data/geo/
COPY data/geo/metadata/zhejiang_boundaries.json data/geo/metadata/
COPY data/platform/siriagent_110_catalog.json data/platform/
COPY data/test_data/*.tle data/test_data/
USER satellite
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3)"
CMD ["uvicorn", "coverage_api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]
