# Vitalis data generation image: Java 17 + Synthea (prebuilt) + Python generators.
# Build:  docker compose build
FROM eclipse-temurin:17-jdk-jammy

# Pin to a release tag for strict reproducibility, e.g. --build-arg SYNTHEA_REF=v3.3.0
ARG SYNTHEA_REF=master
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-pip git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Synthea: clone and pre-build so each run starts quickly
RUN git clone --depth 1 --branch ${SYNTHEA_REF} https://github.com/synthetichealth/synthea.git /opt/synthea \
 && cd /opt/synthea && ./gradlew build -x test --no-daemon
ENV SYNTHEA_HOME=/opt/synthea

# Python env (venv first on PATH, so `python` just works)
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
WORKDIR /app
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-optional.txt

COPY . .
RUN chmod +x scripts/*.sh
CMD ["bash"]
