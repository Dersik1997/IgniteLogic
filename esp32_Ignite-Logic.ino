// --- [1] LIBRARIES ---
#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <ArduinoJson.h> // Penting untuk membuat payload JSON yang rapi

// --- [2] PIN & SENSOR CONFIG ---
#define DHTPIN 23
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

const int lightPin = 35; // LDR terhubung ke GPIO34 (ADC1)

// PIN LED (Sesuai dengan kode Streamlit yang mengirim perintah ini)
const int ledHijau = 26;
const int ledMerah = 14;
const int ledKuning = 27; 

// --- [3] KREDENSIAL & TOPIC MQTT ---
const char* ssid = "KARUHUNS";
const char* password = "boinktea";
const char* mqtt_server = "broker.emqx.io"; // Broker yang digunakan di Streamlit

// Topic harus SAMA PERSIS dengan di app.py
const char* sensorTopic = "Iot/IgniteLogic/sensor"; // PUBLISHER
const char* outputTopic = "Iot/IgniteLogic/output"; // SUBSCRIBER

WiFiClient espClient;
PubSubClient client(espClient);

// ---------------------------
// FUNGSI KONTROL LED
// ---------------------------
void control_led_from_server(String command) {
    // Matikan semua LED terlebih dahulu
    digitalWrite(ledHijau, LOW);
    digitalWrite(ledMerah, LOW);
    digitalWrite(ledKuning, LOW);

    // Kontrol LED berdasarkan perintah string dari Streamlit/Server
    if (command.indexOf("LED_HIJAU") != -1) {
        digitalWrite(ledHijau, HIGH);
        Serial.println("[Control] LED HIJAU ON (Status: Aman)");
    } else if (command.indexOf("LED_KUNING") != -1) {
        digitalWrite(ledKuning, HIGH);
        Serial.println("[Control] LED KUNING ON (Status: Waspada)");
    } else if (command.indexOf("LED_MERAH") != -1) {
        digitalWrite(ledMerah, HIGH);
        Serial.println("[Control] LED MERAH ON (Status: Tidak Aman)");
    }
}

// ---------------------------
// FUNGSI CALLBACK MQTT (MENERIMA PERINTAH DARI SERVER)
// ---------------------------
void mqttCallback(char* topic, byte* payload, unsigned int length) {
    String message;
    for (unsigned int i = 0; i < length; i++) {
        message += (char)payload[i];
    }
    
    Serial.print("[MQTT Control] Command Received: ");
    Serial.println(message);

    if (String(topic) == outputTopic) {
        control_led_from_server(message);
    }
}

// ---------------------------
// KONEKSI ULANG MQTT
// ---------------------------
void reconnect() {
    while (!client.connected()) {
        Serial.print("Attempting MQTT connection...");
        String clientId = "ESP32_IoT_Device-";
        clientId += String(random(0xffff), HEX);
        if (client.connect(clientId.c_str())) {
            Serial.println("connected");
            // Harus Subscribe ke topik output agar bisa menerima perintah kontrol
            client.subscribe(outputTopic);
            Serial.print("Subscribed to control topic: ");
            Serial.println(outputTopic);
        } else {
            Serial.print("failed, rc=");
            Serial.print(client.state());
            Serial.println(" trying again in 5 seconds");
            delay(5000);
        }
    }
}

// ---------------------------
// SETUP
// ---------------------------
void setup() {
    Serial.begin(115200);
    dht.begin();

    // Inisialisasi PIN LED
    pinMode(ledHijau, OUTPUT);
    pinMode(ledMerah, OUTPUT);
    pinMode(ledKuning, OUTPUT);
    digitalWrite(ledHijau, LOW);
    digitalWrite(ledMerah, LOW);
    digitalWrite(ledKuning, LOW);


    setup_wifi();
    
    // Setup MQTT Client
    client.setServer(mqtt_server, 1883);
    client.setCallback(mqttCallback); 
    randomSeed(micros());
}

// ---------------------------
// LOOP UTAMA (BACA SENSOR DAN KIRIM DATA)
// ---------------------------
void loop() {
    if (!client.connected()) reconnect();
    client.loop(); // Memproses pesan masuk (perintah LED)

    // --- BACA SENSOR ---
    float suhu = dht.readTemperature();
    float lembap = dht.readHumidity();
    int rawLight = analogRead(lightPin);  
    
    // Nilai LDR di balik (4095 = Terang) untuk ML consistency
    int light = 4095 - rawLight; 
    
    // Label ESP di set ke 'N/A' karena keputusan ML dibuat di server
    String label = "N/A"; 

    // Pemeriksaan data DHT (penting karena DHT sering gagal baca)
    if (isnan(suhu) || isnan(lembap)) {
        Serial.println("[ERROR] Failed to read from DHT sensor! Retrying in 10s.");
        delay(10000); 
        return;
    }

    // --- BUAT PAYLOAD JSON ---
    // Ukuran buffer JSON (200 bytes cukup untuk data ini)
    StaticJsonDocument<200> doc;
    
    // Kunci JSON HARUS sesuai dengan yang diharapkan Streamlit/ML:
    doc["suhu"] = suhu;
    doc["lembap"] = lembap;
    doc["light"] = light;       
    doc["rawLight"] = rawLight; 
    doc["label"] = label;

    char jsonData[200];
    serializeJson(doc, jsonData);

    // --- KIRIM DATA KE MQTT ---
    if (client.publish(sensorTopic, jsonData)) {
        Serial.println("------------");
        Serial.println("Data successfully sent to Streamlit server.");
        Serial.print("Payload: ");
        Serial.println(jsonData);
    } else {
        Serial.println("[ERROR] Failed to publish data.");
    }

    delay(10000); // Kirim data setiap 10 detik
}

// ---------------------------
// FUNGSI SETUP WIFI
// ---------------------------
void setup_wifi() {
    Serial.println();
    Serial.print("Connecting to ");
    Serial.println(ssid);
    
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.println("");
    Serial.println("WiFi connected");
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());
}