# app.py (Logika Berbasis Aturan Prioritas)

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

# Timezone helper (WIB/WITA/WIT, di sini diatur ke UTC+7)
TZ = timezone(timedelta(hours=7))

# ---------------------------
# module-level queue used by MQTT thread
# ---------------------------
GLOBAL_MQ = queue.Queue()

# ---------------------------
# Streamlit page setup
# ---------------------------

st.set_page_config(page_title="IoT Realtime Dashboard (Rules & CSV Log)", layout="wide")
st.title("💡 Dashboard Monitoring Lingkungan Realtime (Logika Aturan Prioritas)")
st.caption("Prediksi ML dinonaktifkan. Status dikontrol oleh Aturan Prioritas (Suhu/Lembap/Cahaya).")

# ---------------------------
# session_state init
# ---------------------------
if "msg_queue" not in st.session_state:
    st.session_state.msg_queue = GLOBAL_MQ

if "logs" not in st.session_state:
    try:
        if os.path.exists(CSV_LOG_PATH):
            df_initial = pd.read_csv(CSV_LOG_PATH)
            st.session_state.logs = df_initial.to_dict('records')
        else:
            st.session_state.logs = []
    except Exception:
        st.session_state.logs = []

if "last" not in st.session_state:
    st.session_state.last = None

if "mqtt_thread_started" not in st.session_state:
    st.session_state.mqtt_thread_started = False
    
# Muat Model (Hanya untuk debug/penempatan, tidak digunakan untuk prediksi utama)
if "ml_model" not in st.session_state:
    try:
        st.session_state.ml_model = joblib.load(MODEL_PATH)
        st.info(f"Model scikit-learn ({MODEL_PATH}) dimuat tetapi TIDAK digunakan. Menggunakan Logika Aturan Prioritas.")
    except FileNotFoundError:
        st.session_state.ml_model = None
        st.error(f"File model ML tidak ditemukan di: {MODEL_PATH}.")
    except Exception as e:
        st.session_state.ml_model = None
        st.error(f"Error memuat model ML: {e}")

# --- Global Publisher Client ---
@st.cache_resource
def get_publisher_client():
    pubc = mqtt.Client(client_id=f"Streamlit_Publisher_{time.time() * 1000}")
    try:
        pubc.connect(MQTT_BROKER, MQTT_PORT, 60)
        pubc.loop_start()
    except Exception as e:
        st.error(f"Gagal koneksi Publisher MQTT: {e}")
        return None
    return pubc

pub_client = get_publisher_client()

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
    elif "MERAH" in status: # Termasuk kondisi override kritis
        return "red"
    else:
        return "gray"
# ----------------------------------------

# ---------------------------
# Drain queue (process incoming msgs) - LOGIKA UTAMA: ATURAN PRIORITAS
# ---------------------------
def process_queue():
    updated = False
    q = st.session_state.msg_queue
    
    # Ambil nilai ambang batas cahaya (4095 = terang total)
    # 3000 adalah contoh ambang batas "cahaya masuk"
    LIGHT_THRESHOLD = 3000 
    
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
            
            # Ambil data dari ESP32 (Sesuai nama variabel di JSON payload ESP32)
            suhu = float(d.get("suhu", np.nan))
            lembap = float(d.get("lembap", np.nan))
            light = float(d.get("light", np.nan)) # Nilai Light yang DIBALIK (4095=Terang)
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
            # LOGIKA KEPUTUSAN BERDASARKAN ATURAN PRIORITAS
            # =========================================================
            
            prediksi_server_label = "N/A"
            perintah_led = "N/A"
            prediksi_server_raw = "RULE_N/A"
            
            # --- 1. PRIORITY 1: KRITIS / MERAH ---
            # Jika suhu > 30 ATAU kelembaban > 30, paksa MERAH
            if not np.isnan([suhu, lembap]).any() and (suhu > 30.0 or lembap > 30.0):
                prediksi_server_label = "KRITIS - MERAH (Suhu/Lembap Tinggi)"
                perintah_led = "LED_MERAH"
                prediksi_server_raw = "RULE_MERAH_KRITIS"
                
            # --- 2. PRIORITY 2: WASPADA / KUNING ---
            # Jika cahaya masuk (light > 3000), HANYA JIKA TIDAK KRITIS
            elif not np.isnan(light) and light > LIGHT_THRESHOLD:
                prediksi_server_label = "WASPADA - KUNING (Cahaya Masuk)"
                perintah_led = "LED_KUNING"
                prediksi_server_raw = "RULE_KUNING_CAHAYA"

            # --- 3. PRIORITY 3: AMAN / HIJAU ---
            # Default jika tidak ada kondisi yang terpenuhi
            else:
                prediksi_server_label = "Aman - HIJAU"
                perintah_led = "LED_HIJAU"
                prediksi_server_raw = "RULE_AMAN_DEFAULT"

            # --- Kirim Perintah MQTT ---
            if pub_client and perintah_led != "N/A":
                try:
                    pub_client.publish(TOPIC_OUTPUT, perintah_led) 
                    row["perintah_terkirim"] = perintah_led
                except Exception:
                    row["perintah_terkirim"] = "ERROR PUBLISH"
            
            row["prediksi_server"] = prediksi_server_label
            row["prediksi_server_raw"] = prediksi_server_raw 
            # =========================================================

            st.session_state.last = row
            st.session_state.logs.append(row)
            
            if len(st.session_state.logs) > 5000:
                st.session_state.logs = st.session_state.logs[-5000:]
            updated = True
            
    # =========================================================
    # LOGIKA CSV LOGGING
    # =========================================================
    if updated and st.session_state.logs:
        try:
            df_log = pd.DataFrame(st.session_state.logs)
            # Simpan Raw Output untuk keperluan debug dan audit
            df_export = df_log[['ts', 'suhu', 'lembap', 'light', 'rawLight', 'prediksi_server', 'prediksi_server_raw']].copy()
            df_export.to_csv(CSV_LOG_PATH, index=False)
        except Exception:
            pass
            
    return updated

# run once here to pick up immediately available messages
_ = process_queue()

# ---------------------------
# UI layout (Sudah Sesuai)
# ---------------------------
if HAS_AUTOREFRESH:
    st_autorefresh(interval=2000, limit=None, key="autorefresh") 


left, right = st.columns([1, 2])

with left:
    st.header("Connection Status")
    st.write("Broker:", f"**{MQTT_BROKER}:{MQTT_PORT}**")
    connected = getattr(st.session_state, "last_status", None)
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
        st.write(f"Light (Dibalik, 4095=Terang): **{last.get('light')}**")
        st.caption(f"Light Mentah (LDR ADC): **{last.get('rawLight')}**")
        st.markdown("---")

        st.markdown("### Status Keputusan Server")
        pred_text = last.get('prediksi_server', 'N/A')
        pred_color = get_status_color(pred_text)
        
        # Penanganan display untuk Override Kritis
        display_text = pred_text
        if "MERAH" in pred_text and ("Suhu" in pred_text or "Lembap" in pred_text):
            display_text = f"🚨 {pred_text} 🚨"
            
        st.markdown(f"**<p style='font-size: 24px; color: {pred_color};'>{display_text}</p>**", unsafe_allow_html=True)
        
        st.caption(f"Aturan yang Diterapkan: **{last.get('prediksi_server_raw', 'N/A')}**")
        st.caption(f"Perintah Terakhir ke ESP32: **{last.get('perintah_terkirim', 'N/A')}**")

    else:
        st.info("Waiting for data...")

    st.markdown("---")
    st.header("Download Logs")
    st.caption(f"File log otomatis: **{CSV_LOG_PATH}**")
    
    if os.path.exists(CSV_LOG_PATH):
        with open(CSV_LOG_PATH, "r") as file:
            csv_data = file.read().encode("utf-8")
            st.download_button("Download CSV file", data=csv_data, file_name=CSV_LOG_PATH, mime="text/csv")
    else:
        st.info("File log belum ada.")


with right:
    st.header("Live Chart (last 200 points)")
    df_plot = pd.DataFrame(st.session_state.logs[-200:])
    
    if (not df_plot.empty) and {"suhu", "lembap", "light"}.issubset(df_plot.columns):
        
        df_plot["ts"] = pd.to_datetime(df_plot["ts"])
        
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(x=df_plot["ts"], y=df_plot["suhu"], mode="lines+markers", name="Suhu (°C)"))
        fig.add_trace(go.Scatter(x=df_plot["ts"], y=df_plot["lembap"], mode="lines+markers", name="Lembap (%)", yaxis="y2"))
        fig.add_trace(go.Scatter(x=df_plot["ts"], y=df_plot["light"], mode="lines", name="Light (Dibalik)", yaxis="y3", opacity=0.3))

        fig.update_layout(
            yaxis=dict(title="Suhu (°C)", side="left"),
            yaxis2=dict(title="Lembap (%)", overlaying="y", side="right", showgrid=False),
            yaxis3=dict(title="Light (0-4095)", overlaying="y", side="right", showgrid=False, range=[0, 4100], anchor="free", position=0.98),
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
        # Menampilkan Raw Output (Aturan yang Diterapkan) di log
        log_columns = ["ts", "suhu", "lembap", "light", "prediksi_server", "prediksi_server_raw", "perintah_terkirim"]
             
        df_display = pd.DataFrame(st.session_state.logs)[log_columns].rename(columns={
            "light": "Light (Dibalik)",
            "prediksi_server": "Status",
            "prediksi_server_raw": "Aturan Diterapkan",
            "perintah_terkirim": "Perintah Ke ESP32"
        })
        st.dataframe(df_display[::-1].head(100), use_container_width=True)
    else:
        st.write("—")

process_queue()
