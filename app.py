import streamlit as st
import pandas as pd
import numpy as np
import json
import time
import queue
import threading
import paho.mqtt.client as mqtt
from datetime import datetime, timezone, timedelta
import os 
import plotly.graph_objs as go

# ---------------------------
# Config 
# ---------------------------
MQTT_BROKER = "broker.emqx.io"
MQTT_PORT = 1883
TOPIC_SENSOR = "Iot/IgniteLogic/sensor" 
CSV_LOG_PATH = "iot_sensor_data.csv" # File log otomatis

# Timezone helper
TZ = timezone(timedelta(hours=7))

# ---------------------------
# module-level queue used by MQTT thread
# ---------------------------
GLOBAL_MQ = queue.Queue()

# ---------------------------
# Streamlit page setup
# ---------------------------

st.set_page_config(page_title="IoT Data Logger MQTT", layout="wide")
st.title("💾 Data Logger Realtime via MQTT")

# ---------------------------
# session_state init
# ---------------------------
if "msg_queue" not in st.session_state:
    st.session_state.msg_queue = GLOBAL_MQ

if "logs" not in st.session_state:
    # Coba muat data log yang sudah ada dari CSV saat startup
    try:
        if os.path.exists(CSV_LOG_PATH):
            df_initial = pd.read_csv(CSV_LOG_PATH)
            st.session_state.logs = df_initial.to_dict('records')
        else:
            st.session_state.logs = []
    except Exception:
        st.session_state.logs = []

if "mqtt_thread_started" not in st.session_state:
    st.session_state.mqtt_thread_started = False
    
# ---------------------------
# MQTT callbacks & Thread Start
# ---------------------------
def _on_connect(client, userdata, flags, rc):
    try:
        client.subscribe(TOPIC_SENSOR) # Streamlit subscribe ke topik ESP32
    except Exception:
        pass
    GLOBAL_MQ.put({"_type": "status", "connected": (rc == 0), "ts": time.time()})

def _on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="ignore")
    
    try:
        data = json.loads(payload)
    except Exception:
        GLOBAL_MQ.put({"_type": "raw", "payload": payload, "ts": time.time()})
        return

    GLOBAL_MQ.put({"_type": "sensor", "data": data, "ts": time.time(), "topic": msg.topic})

def start_mqtt_thread_once():
    def worker():
        client = mqtt.Client() 
        client.on_connect = _on_connect
        client.on_message = _on_message
        while True:
            try:
                client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
                client.loop_forever()
            except Exception as e:
                GLOBAL_MQ.put({"_type": "error", "msg": f"MQTT worker error: {e}", "ts": time.time()})
                time.sleep(5) 

    if not st.session_state.mqtt_thread_started:
        t = threading.Thread(target=worker, daemon=True, name="mqtt_worker")
        t.start()
        st.session_state.mqtt_thread_started = True
        time.sleep(0.05)

start_mqtt_thread_once()

# ---------------------------
# Drain queue (process incoming msgs) - LOGIKA UTAMA: CSV LOGGING
# ---------------------------
def process_queue():
    updated = False
    q = st.session_state.msg_queue
    while not q.empty():
        item = q.get()
        ttype = item.get("_type")
        
        if ttype == "status":
            st.session_state.last_status = item.get("connected", False)
            updated = True
        
        elif ttype == "error":
            st.error(item.get("msg"))
            updated = True
        
        elif ttype == "sensor":
            d = item.get("data", {})
            
            # Ambil data mentah dari ESP32
            suhu = float(d.get("suhu", np.nan))
            lembap = float(d.get("lembap", np.nan))
            light = int(d.get("light", np.nan)) 
            rawLight = int(d.get("rawLight", np.nan)) 
            
            row = {
                "ts": datetime.fromtimestamp(item.get("ts", time.time()), TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "suhu": suhu,
                "lembap": lembap,
                "light_fix": light, # Ganti nama kolom untuk kejelasan
                "rawLight": rawLight,
                # Tambahkan kolom target kosong untuk diisi manual nanti
                "label_target": "" 
            }
            
            st.session_state.last = row
            st.session_state.logs.append(row)
            
            if len(st.session_state.logs) > 5000:
                st.session_state.logs = st.session_state.logs[-5000:]
            updated = True
            
    # =========================================================
    # LOGIKA OTOMATIS MENULIS KE CSV (SETELAH DATA MASUK)
    # =========================================================
    if updated and st.session_state.logs:
        try:
            df_log = pd.DataFrame(st.session_state.logs)
            
            # Kolom untuk CSV: Data mentah + kolom label kosong
            df_export = df_log[['ts', 'suhu', 'lembap', 'light_fix', 'rawLight', 'label_target']].copy()
            
            # Tulis ke file CSV (menimpa file setiap update)
            df_export.to_csv(CSV_LOG_PATH, index=False)
            
        except Exception:
            pass 
            
    return updated

# run once here to pick up immediately available messages
_ = process_queue()

# ---------------------------
# UI layout
# ---------------------------
# (Streamlit tidak memiliki autorefresh built-in, perlu di-trigger manual atau menggunakan library pihak ketiga)
st.write(f"Last updated: {datetime.now(TZ).strftime('%H:%M:%S')}")
st.write(f"Listening on MQTT Broker: **{MQTT_BROKER}** Topic: **{TOPIC_SENSOR}**")
st.markdown("---")

col1, col2 = st.columns(2)

with col1:
    st.header("Latest Readings")
    if st.session_state.last:
        last = st.session_state.last
        st.metric("Suhu", f"{last.get('suhu')} °C")
        st.metric("Kelembapan", f"{last.get('lembap')} %")
        st.metric("LightFix", f"{last.get('light_fix')} (0-4095)")
    else:
        st.info("Waiting for first data point...")

with col2:
    st.header("Download Log CSV")
    st.caption(f"File log disimpan otomatis ke: **{CSV_LOG_PATH}**")
    
    if st.button("Download Data Log (.csv)"):
        if os.path.exists(CSV_LOG_PATH):
            with open(CSV_LOG_PATH, "r") as file:
                csv_data = file.read().encode("utf-8")
                st.download_button("Download CSV file", data=csv_data, file_name=CSV_LOG_PATH)
        else:
            st.warning("File log belum ada. Tunggu data masuk.")

st.markdown("---")
st.header("Recent Logs (for viewing)")
if st.session_state.logs:
    df_display = pd.DataFrame(st.session_state.logs)[::-1].head(10)
    st.dataframe(df_display, use_container_width=True)
else:
    st.write("— No data received yet —")

# Rerun Streamlit untuk mendapatkan data baru (manual refresh jika tidak ada autorefresh)
if st.button('Refresh Data'):
    st.experimental_rerun()
