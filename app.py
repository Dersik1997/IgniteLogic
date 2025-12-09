import streamlit as st
import pandas as pd
import numpy as np
import joblib
import json
import time
import queue
import threading
from datetime import datetime, timezone, timedelta
import plotly.graph_objs as go
import paho.mqtt.client as mqtt

# Optional: lightweight auto-refresh helper
try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except Exception:
    HAS_AUTOREFRESH = False

# ---------------------------
# Config (edit if needed)
# ---------------------------
MQTT_BROKER = "broker.emqx.io"
MQTT_PORT = 1883
TOPIC_SENSOR = "Iot/IgniteLogic/sensor"
TOPIC_OUTPUT = "Iot/IgniteLogic/output" 
MODEL_PATH = "model.pkl" 

# timezone GMT+7 helper
TZ = timezone(timedelta(hours=7))
def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

# ---------------------------
# module-level queue used by MQTT thread
# ---------------------------
GLOBAL_MQ = queue.Queue()

# ---------------------------
# Streamlit page setup
# ---------------------------

st.set_page_config(page_title="IoT Realtime Dashboard (Aman/Tidak Aman)", layout="wide")
st.title("💡 Dashboard Monitoring Lingkungan Realtime (ESP32)")

# ---------------------------
# session_state init
# ---------------------------
if "msg_queue" not in st.session_state:
    st.session_state.msg_queue = GLOBAL_MQ

if "logs" not in st.session_state:
    st.session_state.logs = [] # list of dict rows

if "last" not in st.session_state:
    st.session_state.last = None

if "mqtt_thread_started" not in st.session_state:
    st.session_state.mqtt_thread_started = False

# Kunci ML model dihapus karena logika sudah di ESP32
if "ml_model" in st.session_state:
    del st.session_state["ml_model"]
    
# ---------------------------
# MQTT callbacks (use GLOBAL_MQ, NOT st.session_state inside callbacks)
# ---------------------------
def _on_connect(client, userdata, flags, rc):
    try:
        client.subscribe(TOPIC_SENSOR)
        # Tambahkan subscribe ke TOPIC_OUTPUT jika ingin melihat feedback balik
        client.subscribe(TOPIC_OUTPUT) 
    except Exception:
        pass
    GLOBAL_MQ.put({"_type": "status", "connected": (rc == 0), "ts": time.time()})

def _on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="ignore")
    
    # Pesan dari TOPIC_OUTPUT (misal: status Aman/Tidak Aman)
    if msg.topic == TOPIC_OUTPUT:
        GLOBAL_MQ.put({"_type": "output", "payload": payload, "ts": time.time()})
        return

    try:
        data = json.loads(payload)
    except Exception:
        GLOBAL_MQ.put({"_type": "raw", "payload": payload, "ts": time.time()})
        return

    # push structured sensor message
    GLOBAL_MQ.put({"_type": "sensor", "data": data, "ts": time.time(), "topic": msg.topic})

# ---------------------------
# Start MQTT thread (worker)
# ---------------------------
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
                time.sleep(5)  # backoff then retry

    if not st.session_state.mqtt_thread_started:
        t = threading.Thread(target=worker, daemon=True, name="mqtt_worker")
        t.start()
        st.session_state.mqtt_thread_started = True
        time.sleep(0.05)

# start thread
start_mqtt_thread_once()

# ---------------------------
# Drain queue (process incoming msgs)
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
        
        # Penanganan pesan OUTPUT dari ESP32 (biasanya status Aman/Tidak Aman)
        elif ttype == "output":
            # Ini bisa dicatat atau diabaikan, tapi untuk saat ini, kita abaikan saja karena data sensor sudah mencakup status.
            pass

        elif ttype == "sensor":
            d = item.get("data", {})
            
            # --- PENTING: Penyesuaian Kunci JSON ---
            try:
                suhu = float(d.get("suhu")) # Mengambil "suhu" dari Arduino
            except Exception:
                suhu = None
            try:
                lembap = float(d.get("lembap")) # Mengambil "lembap" dari Arduino
            except Exception:
                lembap = None
            try:
                light = int(d.get("light")) # Mengambil "light" dari Arduino
            except Exception:
                light = None
            
            status_esp = d.get("status", "N/A") # Mengambil "status" dari Arduino

            row = {
                "ts": datetime.fromtimestamp(item.get("ts", time.time()), TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "suhu": suhu,
                "lembap": lembap,
                "light": light,
                "status_esp": status_esp # Status dari logika di ESP32
            }
            
            # --- Nonaktifkan Logika ML/Anomaly (Karena sudah ada di ESP32) ---
            row["pred"] = status_esp # Gunakan status ESP32 sebagai 'prediksi'
            row["conf"] = 1.0 if status_esp in ["Aman", "Tidak Aman"] else None
            row["anomaly"] = False
            # -----------------------------------------------------------------

            st.session_state.last = row
            st.session_state.logs.append(row)
            
            # keep bounded
            if len(st.session_state.logs) > 5000:
                st.session_state.logs = st.session_state.logs[-5000:]
            updated = True
            
            # Tidak perlu auto-publish alert karena logika penentuan sudah ada di ESP32
            # Jika Anda ingin Streamlit menimpa status (misal: mengaktifkan fan override),
            # Anda bisa menambahkan logika publish di sini.
    return updated

# run once here to pick up immediately available messages
_ = process_queue()

# ---------------------------
# UI layout
# ---------------------------
# optionally auto refresh UI; requires streamlit-autorefresh in requirements
if HAS_AUTOREFRESH:
    # Refresh setiap 2 detik, sinkron dengan delay di Arduino
    st_autorefresh(interval=2000, limit=None, key="autorefresh") 

left, right = st.columns([1, 2])

with left:
    st.header("Connection Status")
    st.write("Broker:", f"{MQTT_BROKER}:{MQTT_PORT}")
    connected = getattr(st.session_state, "last_status", None)
    st.metric("MQTT Connected", "Yes" if connected else "No")
    st.write("Topic Sensor:", TOPIC_SENSOR)
    st.write("Topic Output:", TOPIC_OUTPUT)
    st.markdown("---")

    st.header("Last Reading")
    if st.session_state.last:
        last = st.session_state.last
        st.write(f"Time: **{last.get('ts')}**")
        st.write(f"Light: **{last.get('light')}** (0-4095)")
        st.write(f"Suhu: **{last.get('suhu')} °C**")
        st.write(f"Lembap: **{last.get('lembap')} %**")
        st.markdown("### Status ESP32")
        
        status_color = "green" if last.get('status_esp') == "Aman" else "red"
        st.markdown(f"**<p style='font-size: 24px; color: {status_color};'>{last.get('status_esp', 'N/A')}</p>**", unsafe_allow_html=True)
        
    else:
        st.info("Waiting for data...")

    st.markdown("---")
    st.header("Manual Output Control")
    col1, col2 = st.columns(2)
    # Gunakan status Aman/Tidak Aman sesuai logika ESP32
    if col1.button("Send Aman"):
        try:
            pubc = mqtt.Client()
            pubc.connect(MQTT_BROKER, MQTT_PORT, 60)
            pubc.publish(TOPIC_OUTPUT, "Aman")
            pubc.disconnect()
            st.success("Published Aman")
        except Exception as e:
            st.error(f"Publish failed: {e}")
    if col2.button("Send Tidak Aman"):
        try:
            pubc = mqtt.Client()
            pubc.connect(MQTT_BROKER, MQTT_PORT, 60)
            pubc.publish(TOPIC_OUTPUT, "Tidak Aman")
            pubc.disconnect()
            st.success("Published Tidak Aman")
        except Exception as e:
            st.error(f"Publish failed: {e}")

    st.markdown("---")
    st.header("Download Logs")
    if st.button("Download CSV"):
        if st.session_state.logs:
            # Sesuaikan kolom untuk log
            df_dl = pd.DataFrame(st.session_state.logs)
            csv = df_dl.to_csv(index=False).encode("utf-8")
            st.download_button("Download CSV file", data=csv, file_name=f"iot_logs_{int(time.time())}.csv")
        else:
            st.info("No logs to download")

with right:
    st.header("Live Chart (last 200 points)")
    df_plot = pd.DataFrame(st.session_state.logs[-200:])
    
    # Kunci kolom disesuaikan menjadi "suhu" dan "lembap"
    if (not df_plot.empty) and {"suhu", "lembap", "light"}.issubset(df_plot.columns):
        fig = go.Figure()
        
        # Suhu
        fig.add_trace(go.Scatter(x=df_plot["ts"], y=df_plot["suhu"], mode="lines+markers", name="Suhu (°C)"))
        
        # Kelembaban
        fig.add_trace(go.Scatter(x=df_plot["ts"], y=df_plot["lembap"], mode="lines+markers", name="Lembap (%)", yaxis="y2"))
        
        # Cahaya (Light) - sebagai Y3
        fig.add_trace(go.Scatter(x=df_plot["ts"], y=df_plot["light"], mode="lines", name="Light (0-4095)", yaxis="y3", opacity=0.3))


        fig.update_layout(
            yaxis=dict(title="Suhu (°C)", side="left"),
            yaxis2=dict(title="Lembap (%)", overlaying="y", side="right", showgrid=False),
            yaxis3=dict(title="Light", overlaying="y", side="right", showgrid=False, range=[0, 4100], anchor="free", position=0.98), # Tambahkan Y3
            height=520,
            hovermode="x unified"
        )
        
        # color markers by status ESP32
        colors = []
        for _, r in df_plot.iterrows():
            stat = r.get("status_esp", "")
            if stat == "Aman":
                colors.append("green")
            elif stat == "Tidak Aman":
                colors.append("red")
            else:
                colors.append("gray")
                
        # Terapkan pewarnaan hanya pada Suhu dan Lembap (Trace 0 dan 1)
        fig.update_traces(marker=dict(size=8, color=colors), selector=dict(mode="lines+markers", name="Suhu (°C)"))
        fig.update_traces(marker=dict(size=8, color=colors), selector=dict(mode="lines+markers", name="Lembap (%)"))
        
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No data yet. Make sure ESP32 publishes to correct topic.")

    st.markdown("### Recent Logs")
    if st.session_state.logs:
        # Tampilkan kolom yang relevan
        df_display = pd.DataFrame(st.session_state.logs)[["ts", "light", "suhu", "lembap", "status_esp"]].rename(columns={
            "status_esp": "Status ESP32"
        })
        st.dataframe(df_display[::-1].head(100), use_container_width=True)
    else:
        st.write("—")

# after UI render, drain queue (so next rerun shows fresh data)
process_queue()

