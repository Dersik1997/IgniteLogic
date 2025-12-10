import streamlit as st
import pandas as pd
import numpy as np
import joblib 
import json
import time
import queue
import threading
import paho.mqtt.client as mqtt
from datetime import datetime, timezone, timedelta
import plotly.graph_objs as go
import os 
import sys # Digunakan untuk debugging

# Optional: lightweight auto-refresh helper
try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except Exception:
    HAS_AUTOREFRESH = False

# ---------------------------
# Config 
# ---------------------------
MQTT_BROKER = "broker.emqx.io"
MQTT_PORT = 1883
TOPIC_SENSOR = "Iot/IgniteLogic/sensor"
TOPIC_OUTPUT = "Iot/IgniteLogic/output" 
MODEL_PATH = "model.pkl" 
CSV_LOG_PATH = "iot_sensor_data.csv" 

# Timezone helper
TZ = timezone(timedelta(hours=7))

# ---------------------------
# module-level queue used by MQTT thread
# ---------------------------
GLOBAL_MQ = queue.Queue()

# ---------------------------
# Streamlit page setup
# ---------------------------

st.set_page_config(page_title="IoT Realtime Dashboard (scikit-learn + CSV Log)", layout="wide")
st.title("💡 Dashboard Monitoring Lingkungan Realtime (Prediksi scikit-learn & CSV Log)")
st.caption("ESP32 mengirim data mentah. Server (Streamlit) membuat prediksi ML, mengirim perintah LED balik, dan menyimpan log.")

# ---------------------------
# session_state init (Perbaikan Utama di SINI)
# ---------------------------
if "msg_queue" not in st.session_state:
    st.session_state.msg_queue = GLOBAL_MQ

# --- PERBAIKAN ERROR ATTRIBUTE: Pastikan 'last' dan 'last_status' diinisialisasi ---
if "last" not in st.session_state:
    st.session_state.last = None 
if "last_status" not in st.session_state:
    st.session_state.last_status = False
# -----------------------------------------------------------------------------------

if "logs" not in st.session_state:
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
    
# Inisialisasi/Muat Model scikit-learn
if "ml_model" not in st.session_state:
    try:
        st.session_state.ml_model = joblib.load(MODEL_PATH)
        st.info(f"Model scikit-learn ({MODEL_PATH}) berhasil dimuat.")
    except FileNotFoundError:
        st.session_state.ml_model = None
        st.error(f"File model ML tidak ditemukan di: {MODEL_PATH}. Prediksi server dinonaktifkan.")
    except Exception as e:
        st.session_state.ml_model = None
        st.error(f"Error memuat model ML: {e}")

# ---------------------------
# MQTT callbacks & Thread Start
# ---------------------------
def _on_connect(client, userdata, flags, rc):
    try:
        client.subscribe(TOPIC_SENSOR)
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

# --- Helper function for status color ---
def get_status_color(status):
    if "Aman" in status or "HIJAU" in status:
        return "green"
    elif "Waspada" in status or "KUNING" in status:
        return "orange"
    elif "Tidak Aman" in status or "MERAH" in status:
        return "red"
    else:
        return "gray"
# ----------------------------------------

# ---------------------------
# Drain queue (process incoming msgs) - LOGIKA UTAMA: PREDIKSI, KONTROL & CSV LOGGING
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
            
            suhu = float(d.get("suhu", np.nan))
            lembap = float(d.get("lembap", np.nan))
            light = int(d.get("light", np.nan)) 
            rawLight = int(d.get("rawLight", np.nan)) 
            status_esp = d.get("label", "N/A") 
            
            row = {
                "ts": datetime.fromtimestamp(item.get("ts", time.time()), TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "suhu": suhu,
                "lembap": lembap,
                "light": light,
                "rawLight": rawLight,
                "status_esp": status_esp, 
                "prediksi_server": "N/A",
                "perintah_terkirim": "N/A"
            }
            
            # =========================================================
            # LOGIKA PREDIKSI SCKIT-LEARN (SERVER)
            # =========================================================
            prediksi_server = "N/A"
            
            if st.session_state.ml_model and not np.isnan([suhu, lembap, light]).any():
                try:
                    # Input Model: [suhu, lembap, light (dibalik)]
                    fitur_input = np.array([[np.float64(suhu), np.float64(lembap), np.float64(light)]]) 
                    
                    # Prediksi
                    prediksi_server = st.session_state.ml_model.predict(fitur_input)[0]
                    
                    # --- KONTROL LED BALIK KE ESP32 ---
                    perintah_led = ""
                    if "Aman" in prediksi_server:
                        perintah_led = "LED_HIJAU"
                    elif "Waspada" in prediksi_server:
                        perintah_led = "LED_KUNING"
                    else: 
                        perintah_led = "LED_MERAH"
                        
                    try:
                        pubc = mqtt.Client()
                        pubc.connect(MQTT_BROKER, MQTT_PORT, 60)
                        pubc.publish(TOPIC_OUTPUT, perintah_led) 
                        pubc.disconnect()
                        row["perintah_terkirim"] = perintah_led
                    except Exception:
                        row["perintah_terkirim"] = "ERROR PUBLISH" 

                except Exception as e:
                    # Jika terjadi error saat prediksi (misal: format input salah), catat errornya
                    row["prediksi_server"] = f"ML Error: {e}" 
            
            row["prediksi_server"] = str(prediksi_server)
            # =========================================================

            st.session_state.last = row
            st.session_state.logs.append(row)
            
            if len(st.session_state.logs) > 5000:
                st.session_state.logs = st.session_state.logs[-5000:]
            updated = True
            
    # =========================================================
    # LOGIKA OTOMATIS MENULIS KE CSV (Setelah data diproses)
    # =========================================================
    if updated and st.session_state.logs:
        try:
            df_log = pd.DataFrame(st.session_state.logs)
            
            df_export = df_log[['ts', 'suhu', 'lembap', 'light', 'rawLight', 'prediksi_server']].copy()
            
            df_export.to_csv(CSV_LOG_PATH, index=False)
            
        except Exception:
            pass 
            
    return updated

# run once here to pick up immediately available messages
_ = process_queue()

# ---------------------------
# UI layout
# ---------------------------
if HAS_AUTOREFRESH:
    st_autorefresh(interval=2000, limit=None, key="autorefresh") 


left, right = st.columns([1, 2])

with left:
    st.header("Connection Status")
    # Perbaikan: Mengakses st.session_state.last_status dengan aman
    connected = st.session_state.get("last_status", False) 
    st.write("Broker:", f"**{MQTT_BROKER}:{MQTT_PORT}**")
    st.metric("MQTT Connected", "Yes" if connected else "No")
    st.write("Topic Sensor (Input):", TOPIC_SENSOR)
    st.write("Topic Output (Control):", TOPIC_OUTPUT)
    st.markdown("---")

    st.header("Last Reading")
    if st.session_state.last:
        last = st.session_state.last
        st.write(f"Time: **{last.get('ts')}**")
        st.write(f"Suhu: **{last.get('suhu')} °C**")
        st.write(f"Lembap: **{last.get('lembap')} %**")
        st.write(f"Light (Dibalik): **{last.get('light')}**")
        st.markdown("---")

        st.markdown("### Prediksi Server (scikit-learn)")
        pred_text = last.get('prediksi_server', 'N/A')
        pred_color = get_status_color(pred_text)
        st.markdown(f"**<p style='font-size: 24px; color: {pred_color};'>● {pred_text}</p>**", unsafe_allow_html=True)
        
        st.caption(f"Perintah Terakhir ke ESP32: **{last.get('perintah_terkirim', 'N/A')}**")

    else:
        st.info("Waiting for data...")

    st.markdown("---")
    st.header("Download Logs")
    st.caption(f"File log otomatis: **{CSV_LOG_PATH}**")
    
    if st.button("Download CSV Log"):
        if os.path.exists(CSV_LOG_PATH):
            with open(CSV_LOG_PATH, "r") as file:
                csv_data = file.read().encode("utf-8")
                st.download_button("Download CSV file", data=csv_data, file_name=CSV_LOG_PATH)
        else:
            st.info("File log belum ada. Tunggu data masuk.")


with right:
    st.header("Live Chart (last 200 points)")
    df_plot = pd.DataFrame(st.session_state.logs[-200:])
    
    if (not df_plot.empty) and {"suhu", "lembap", "light"}.issubset(df_plot.columns):
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(x=df_plot["ts"], y=df_plot["suhu"], mode="lines+markers", name="Suhu (°C)"))
        fig.add_trace(go.Scatter(x=df_plot["ts"], y=df_plot["lembap"], mode="lines+markers", name="Lembap (%)", yaxis="y2"))
        fig.add_trace(go.Scatter(x=df_plot["ts"], y=df_plot["light"], mode="lines", name="Light (0-4095)", yaxis="y3", opacity=0.3))

        fig.update_layout(
            yaxis=dict(title="Suhu (°C)", side="left"),
            yaxis2=dict(title="Lembap (%)", overlaying="y", side="right", showgrid=False),
            yaxis3=dict(title="Light", overlaying="y", side="right", showgrid=False, range=[0, 4100], anchor="free", position=0.98),
            height=520,
            hovermode="x unified"
        )
        
        colors = []
        for _, r in df_plot.iterrows():
            stat = r.get("prediksi_server", "")
            colors.append(get_status_color(stat))
            
        fig.update_traces(marker=dict(size=8, color=colors), selector=dict(mode="lines+markers", name="Suhu (°C)"))
        fig.update_traces(marker=dict(size=8, color=colors), selector=dict(mode="lines+markers", name="Lembap (%)"))
        
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No data yet. Make sure ESP32 publishes to correct topic.")


    st.markdown("### Recent Logs")
    if st.session_state.logs:
        df_display = pd.DataFrame(st.session_state.logs)[["ts", "suhu", "lembap", "light", "prediksi_server", "perintah_terkirim"]].rename(columns={
            "light": "Light (Dibalik)",
            "prediksi_server": "Prediksi Server (ML)",
            "perintah_terkirim": "Perintah Ke ESP32"
        })
        st.dataframe(df_display[::-1].head(100), use_container_width=True)
    else:
        st.write("—")

process_queue()
