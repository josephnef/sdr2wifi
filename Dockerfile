# Reproducible build + test environment for sdr2wifi (no SDR/UHD needed).
#   docker build -t sdr2wifi . && docker run --rm sdr2wifi
# Builds GNU Radio 3.10 (apt) + gr-foo + the gr-ieee802-11 fork at the pinned refs
# in deps.env, then runs the asserting synthetic test matrix.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV GRWIFI_PREFIX=/opt/grwifi

RUN apt-get update && apt-get install -y --no-install-recommends \
        gnuradio gnuradio-dev \
        cmake build-essential git ca-certificates \
        python3 python3-dev python3-numpy pybind11-dev \
        libvolk-dev libboost-all-dev liborc-0.4-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# Build the OOT deps in a layer cached on the pinned refs + the build script, so
# editing the harness/tests doesn't trigger a full dependency rebuild.
COPY deps.env ./deps.env
COPY scripts/build-deps.sh ./scripts/build-deps.sh
RUN bash scripts/build-deps.sh

# The harness + tests.
COPY . .

CMD ["bash", "scripts/run-tests.sh"]
