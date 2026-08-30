#!/usr/bin/with-contenv bashio
set -Eeuo pipefail

export BLINKT_LOG_LEVEL="$(bashio::config 'log_level')"
export BLINKT_DISCOVERY_PREFIX="$(bashio::config 'discovery_prefix')"
export BLINKT_TOPIC_PREFIX="$(bashio::config 'topic_prefix')"
export BLINKT_DEVICE_ID="$(bashio::config 'device_id')"
export BLINKT_DEVICE_NAME="$(bashio::config 'device_name')"
export BLINKT_DEFAULT_BRIGHTNESS="$(bashio::config 'default_brightness')"
export BLINKT_RESTORE_STATE="$(bashio::config 'restore_state')"
export BLINKT_CLEAR_ON_STOP="$(bashio::config 'clear_on_stop')"
export BLINKT_ORIENTATION="$(bashio::config 'orientation')"
export BLINKT_GPIO_CHIP="$(bashio::config 'gpio_chip')"
export BLINKT_DATA_DIR="/data"

mqtt_host="$(bashio::config 'mqtt_host')"
if [[ "${mqtt_host}" == "auto" ]]; then
    if ! bashio::services.available 'mqtt'; then
        bashio::log.fatal \
            "mqtt_host is 'auto', but no MQTT service is available. Start the Mosquitto broker app or configure a broker manually."
    fi

    export BLINKT_MQTT_HOST="$(bashio::services mqtt 'host')"
    export BLINKT_MQTT_PORT="$(bashio::services mqtt 'port')"
    export BLINKT_MQTT_USERNAME="$(bashio::services mqtt 'username')"
    export BLINKT_MQTT_PASSWORD="$(bashio::services mqtt 'password')"
    export BLINKT_MQTT_TLS="$(bashio::services mqtt 'ssl')"
    bashio::log.info "Using the MQTT service advertised by Home Assistant Supervisor"
else
    export BLINKT_MQTT_HOST="${mqtt_host}"
    export BLINKT_MQTT_PORT="$(bashio::config 'mqtt_port')"
    export BLINKT_MQTT_USERNAME="$(bashio::config 'mqtt_username')"
    export BLINKT_MQTT_PASSWORD="$(bashio::config 'mqtt_password')"
    export BLINKT_MQTT_TLS="$(bashio::config 'mqtt_tls')"
    bashio::log.info "Using the manually configured MQTT broker"
fi

export BLINKT_MQTT_TLS_INSECURE="$(bashio::config 'mqtt_tls_insecure')"
ca_file="$(bashio::config 'mqtt_ca_file')"
if [[ -n "${ca_file}" ]]; then
    if [[ "${ca_file}" == /* ]]; then
        export BLINKT_MQTT_CA_FILE="${ca_file}"
    else
        export BLINKT_MQTT_CA_FILE="/ssl/${ca_file}"
    fi
    if [[ ! -f "${BLINKT_MQTT_CA_FILE}" ]]; then
        bashio::log.fatal "MQTT CA file does not exist: ${BLINKT_MQTT_CA_FILE}"
    fi
else
    export BLINKT_MQTT_CA_FILE=""
fi

bashio::log.info "Starting Pimoroni Blinkt MQTT controller"
exec python3 /app/controller.py

