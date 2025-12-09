import streamlit as st
import paho.mqtt.client as mqtt
import json
import time
from datetime import datetime
import pytz

# ============================
# MQTT CONFIG
# ============================
MQTT_BROKER = "broker.emqx.io"
MQTT_PORT = 1883

TOPIC_SENSOR = "Iot/IgniteLogic/sensor"
TOPIC_OUTPUT = "Iot/IgniteLogic/output"

TZ = pytz.timezone("Asia/Jakarta")

# ============================
# RULE PREDIKSI SESUAI DATASET
# ============================
def rule_predict(data):
    temp = float(data.get("temp", 0))
    hum = float(data.get("hum", 0))
    light = float(data.get("light", 0))

    # RULE dari dataset
    if light >= 4000:
        return "Tidak Aman"
    if light == 0:
        return "Aman"
    if temp >= 30 and hum >= 90:
        return "Tidak Aman"
    return "Aman"


# =======================================
# Initialize session state
# =======================================
if "logs" not in st.session_state:
    st.session_state.logs = []

if "last" not in st.session_state:
    st.session_state.last = None


# =======================================
# MQTT CALLBACK HANDLER
# =======================================
def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode()
        data = json.loads(payload)
        
        # Pastikan format: {"ts":..., "type":"sensor", "data": {...}}
        if data.get("type") == "sensor":
            d = data.get("data", {})

            temp = float(d.get("temp", 0))
            hum = float(d.get("hum", 0))
            light = float(d.get("light", 0))

            pred = rule_predict(d)

            row = {
                "timestamp": datetime.fromtimestamp(data.get("ts", time.time()), TZ)
                                .strftime("%Y-%m-%d %H:%M:%S"),
                "temp": temp,
                "hum": hum,
                "light": light,
                "status": pred
            }

            st.session_state.last = row
            st.session_state.logs.append(row)

            # Auto kirim output ke ESP32
            try:
                pubc = mqtt.Client()
                pubc.connect(MQTT_BROKER, MQTT_PORT, 60)
                if pred == "Tidak Aman":
                    pubc.publish(TOPIC_OUTPUT, "RED")
                else:
                    pubc.publish(TOPIC_OUTPUT, "GREEN")
                pubc.disconnect()
            except:
                pass

    except Exception as e:
        print("MQTT Error:", e)


# =======================================
# START MQTT CLIENT
# =======================================
client = mqtt.Client()
client.on_message = on_message
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.subscribe(TOPIC_SENSOR)
client.loop_start()


# =======================================
# STREAMLIT UI
# =======================================
st.title("🔥 IgniteLogic - Monitoring Sensor IoT")

st.markdown("#### Status Sensor Real-time")


# ============================
# Menampilkan data terakhir
# ============================
if st.session_state.last:
    last = st.session_state.last

    st.subheader("📌 Data Terbaru")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Temperature", f"{last['temp']:.2f} °C")
    col2.metric("Humidity", f"{last['hum']:.2f} %")
    col3.metric("Light", f"{last['light']}")
    col4.metric("Status", last["status"])

    # Alert warna
    if last["status"] == "Aman":
        st.success("🟢 Kondisi Aman")
    else:
        st.error("🔴 Tidak Aman!")

else:
    st.info("Menunggu data dari ESP32...")


# ============================
# LOG TABLE
# ============================
st.subheader("📄 Riwayat Data Sensor")

if len(st.session_state.logs) > 0:
    st.dataframe(st.session_state.logs, use_container_width=True)
else:
    st.write("Belum ada data.")


# ============================
# AUTO REFRESH
# ============================
st.experimental_rerun()
