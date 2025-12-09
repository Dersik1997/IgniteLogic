# app.py - FIXED VERSION FOR ESP32 JSON (light, suhu, lembap)

import streamlit as st
import pandas as pd
import numpy as np
import json
import time
import queue
import threading
from datetime import datetime, timezone, timedelta
import plotly.graph_objs as go
import paho.mqtt.client as mqtt
import joblib

# --------------------------------
# CONFIG
# --------------------------------
MQTT_BROKER = "broker.emqx.io"
MQTT_PORT = 1883
TOPIC_SENSOR = "Iot/IgniteLogic/sensor"
TOPIC_OUTPUT = "Iot/IgniteLogic/output"
MODEL_PATH = "model.pkl"

TZ = timezone(timedelta(hours=7))
def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

GLOBAL_MQ = queue.Queue()

st.set_page_config(page_title="IgniteLogic ML Dashboard", layout="wide")
st.title("🔥 IgniteLogic IoT + Machine Learning Dashboard (Stable Version)")

# --------------------------------
# SESSION STATE
# --------------------------------
if "logs" not in st.session_state:
    st.session_state.logs = []

if "last" not in st.session_state:
    st.session_state.last = None

if "mqtt_thread_started" not in st.session_state:
    st.session_state.mqtt_thread_started = False

if "ml_model" not in st.session_state:
    st.session_state.ml_model = joblib.load(MODEL_PATH)


st.success("Model loaded: model.pkl")


# --------------------------------
# MQTT CALLBACKS
# --------------------------------
def _on_connect(client, userdata, flags, rc):
    client.subscribe(TOPIC_SENSOR)
    GLOBAL_MQ.put({"_type": "status", "connected": (rc == 0)})


def _on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        GLOBAL_MQ.put({"_type": "sensor", "data": payload})
    except:
        pass


# --------------------------------
# MQTT THREAD STARTER
# --------------------------------
def start_mqtt_thread_once():

    def worker():
        client = mqtt.Client()
        client.on_connect = _on_connect
        client.on_message = _on_message

        while True:
            try:
                client.connect(MQTT_BROKER, MQTT_PORT, 60)
                client.loop_forever()
            except:
                time.sleep(3)

    if not st.session_state.mqtt_thread_started:
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        st.session_state.mqtt_thread_started = True


start_mqtt_thread_once()


# --------------------------------
# ML PREDICTOR
# --------------------------------
def predict_status(light, temp, hum):
    model = st.session_state.ml_model
    X = [[light, temp, hum]]

    try:
        label = model.predict(X)[0]
    except:
        label = "ERR"

    if hasattr(model, "predict_proba"):
        try:
            conf = float(np.max(model.predict_proba(X)))
        except:
            conf = None
    else:
        conf = None

    return label, conf


# --------------------------------
# PROCESS INCOMING MQTT QUEUE
# --------------------------------
def process_queue():
    q = GLOBAL_MQ

    while not q.empty():
        item = q.get()
        t = now_str()

        if item["_type"] == "sensor":

            d = item["data"]

            # FULL FIX → Menerima JSON dari ESP32
            light = float(d.get("light", 0))

            # ESP32 field = "suhu" dan "lembap"
            temp = float(d.get("temperature", d.get("suhu", 0)))
            hum = float(d.get("humidity", d.get("lembap", 0)))

            pred, conf = predict_status(light, temp, hum)

            # Kirim output ke MQTT
            out_msg = "AMAN" if pred == "Aman" else "TIDAK_AMAN"
            try:
                pub = mqtt.Client()
                pub.connect(MQTT_BROKER, MQTT_PORT, 60)
                pub.publish(TOPIC_OUTPUT, out_msg)
                pub.disconnect()
            except:
                pass

            row = {
                "ts": t,
                "light": light,
                "temp": temp,
                "hum": hum,
                "pred": pred,
                "conf": conf,
            }

            st.session_state.last = row
            st.session_state.logs.append(row)

            if len(st.session_state.logs) > 2000:
                st.session_state.logs = st.session_state.logs[-2000:]


process_queue()


# --------------------------------
# UI DISPLAY
# --------------------------------
left, right = st.columns([1, 2])

# LEFT PANEL (Last Sensor Reading)
with left:
    st.header("📡 Sensor Terakhir")

    if st.session_state.last:
        last = st.session_state.last

        st.write(f"⏱ Waktu: {last['ts']}")
        st.write(f"💡 Light: {last['light']}")
        st.write(f"🌡 Suhu: {last['temp']} °C")
        st.write(f"💧 Lembap: {last['hum']} %")

        st.markdown("---")
        st.subheader("ML Prediction")

        if last["pred"] == "Aman":
            st.success(f"🟢 Status: {last['pred']}")
        else:
            st.error(f"🔴 Status: {last['pred']}")

        st.write(f"Confidence: {last['conf']}")

    else:
        st.info("Menunggu data dari ESP32...")


# RIGHT PANEL (Charts + Logs)
with right:
    st.header("📊 Live Chart")

    df = pd.DataFrame(st.session_state.logs[-200:])

    if not df.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df["ts"], y=df["temp"], mode="lines", name="Suhu"))
        fig.add_trace(go.Scatter(x=df["ts"], y=df["hum"], mode="lines", name="Lembap"))
        fig.add_trace(go.Scatter(x=df["ts"], y=df["light"], mode="lines", name="Light"))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Recent Logs")
    if not df.empty:
        st.dataframe(df[::-1])

