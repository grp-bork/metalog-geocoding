# Geocoding HTTP service -- see service/README.md.
#
# Build from the repository root, since the image needs metalog_geocoding/
# and pyproject.toml as well as service/:
#
#   docker build -f service/Dockerfile -t metalog-geocoding-service .
#
# The boundary-layer parquet is not part of the image; it is mounted as a
# volume (see docs/installation-and-data.md).
FROM python:3.14-trixie

ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Only what the package build needs; scripts/ is for checkouts.
COPY pyproject.toml README.md /app/
COPY metalog_geocoding /app/metalog_geocoding
COPY service /app/service

# [geo] is the geopandas stack, [service] adds Flask and gunicorn. All of it
# installs from wheels on this base image, so no compiler or GDAL headers are
# needed (see docs/development.md).
RUN pip install --no-cache-dir '.[geo,service]'

# Where the boundary layers are expected to be mounted.
ENV METALOG_GEOCODING_DATA=/data

# The SQLite result caches are written to; mount a volume here, or every restart
# starts cold.
ENV METALOG_GEOCODING_CACHE_DIR=/cache

EXPOSE 8007

# No authentication and no TLS: keep the port on an internal network.
#
# --workers 1: each worker would load its own ~1.2 GB of boundary layers.
# Scale with more containers, each with its own cache directory.
# --threads 4: geocoding is serialised by a lock in app.py, so the extra
# threads cost no memory; they let /health answer while a long batch runs and
# another waits for the lock (a client retry after its own timeout), rather
# than queueing behind them and marking the container unhealthy.
# --timeout 600: a cold batch of thousands of coordinates can take minutes
# (the client's default timeout is 300 s).
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--timeout", "600", "--bind", "0.0.0.0:8007", \
     "--chdir", "/app/service", "wsgi:app"]
