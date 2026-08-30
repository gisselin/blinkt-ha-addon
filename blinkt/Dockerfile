FROM ghcr.io/home-assistant/base:3.22

ARG BUILD_VERSION=1.0.0
ARG BUILD_ARCH=aarch64

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}" \
    org.opencontainers.image.title="Pimoroni Blinkt MQTT" \
    org.opencontainers.image.description="MQTT controller for a Pimoroni Blinkt attached to Home Assistant OS" \
    org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYPI=1

COPY requirements.txt /tmp/requirements.txt

RUN apk add --no-cache \
        python3 \
        py3-pip \
        libstdc++ \
    && apk add --no-cache --virtual .build-dependencies \
        build-base \
        linux-headers \
        python3-dev \
        swig \
    && python3 -m pip install \
        --no-cache-dir \
        --break-system-packages \
        --no-deps \
        -r /tmp/requirements.txt \
    && apk del .build-dependencies \
    && rm -f /tmp/requirements.txt

WORKDIR /app
COPY controller.py /app/controller.py
COPY run.sh /run.sh
RUN chmod 0755 /run.sh

CMD ["/run.sh"]

