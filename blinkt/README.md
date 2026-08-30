# Pimoroni Blinkt MQTT

Control a Pimoroni Blinkt! attached directly to the Home Assistant OS
Raspberry Pi. The app exposes one master RGB light and eight individually
controllable RGB pixel lights through MQTT Discovery.

The last state is stored in the app's persistent `/data` volume. Discovery and
state messages are retained and re-published whenever MQTT reconnects or Home
Assistant announces that it is online.

