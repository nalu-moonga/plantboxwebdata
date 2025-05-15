# app.py
from flask import Flask, render_template, jsonify
import paho.mqtt.client as mqtt
import json
import time
import threading
from datetime import datetime
from dateutil.parser import parse
import pytz
import collections

app = Flask(__name__)

# Configuration
TTN_APP_ID = "plant-data@ttn"  # Replace with your TTN app ID
TTN_API_KEY = "NNSXS.SUFBQH4J3UNWANYVWQRGM4HQ6RRBKWUOJ5OBEKQ.DTGXKCCN7NG66NQYKM3VGUKY7JKQDFP3JC6ZBBKTCLSVQUZJGFQQ"     # Replace with your TTN API key
BROKER = "nam1.cloud.thethings.network"
PORT = 1883
TOPIC = f"v3/{TTN_APP_ID}/devices/+/up"

# Data storage
# Using a deque with a max length to avoid memory issues
MAX_RECORDS = 100
data_lock = threading.Lock()
sensor_data = collections.deque(maxlen=MAX_RECORDS)

# MQTT Client
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to TTN MQTT broker")
        client.subscribe(TOPIC)
    else:
        print(f"Failed to connect to MQTT broker with code {rc}")

def on_message(client, userdata, message):
    try:
        # Parse the message
        payload = message.payload.decode()
        js = json.loads(payload)
        
        # Extract timestamp and convert to Eastern Time
        ts = parse(js["received_at"]).astimezone(pytz.timezone("US/Eastern"))
        
        # Extract the decoded payload
        decoded = js["uplink_message"]["decoded_payload"]
        
        # Create record with all the data you need
        record = {
            "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "device_id": js["end_device_ids"]["device_id"],
            # Add all the sensor fields you need
            "temperature": decoded.get("temp"),
            # Add any other fields from your data
            "raw_payload": payload  # Store the raw payload for debugging
        }
        
        # Thread-safe add to our data collection
        with data_lock:
            sensor_data.appendleft(record)
            
        print(f"New data received from {record['device_id']}")
        
    except Exception as e:
        print(f"Error processing message: {e}")

# Start MQTT client in a background thread
def mqtt_client_thread():
    client = mqtt.Client("PythonWebDashboard")
    client.username_pw_set(TTN_APP_ID, password=TTN_API_KEY)
    client.on_connect = on_connect
    client.on_message = on_message
    
    while True:
        try:
            client.connect(BROKER, PORT, 60)
            client.loop_forever()
        except Exception as e:
            print(f"MQTT connection error: {e}")
            print("Reconnecting in 10 seconds...")
            time.sleep(10)

# Start the MQTT client thread when the app starts
mqtt_thread = threading.Thread(target=mqtt_client_thread, daemon=True)
mqtt_thread.start()

# Flask routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    with data_lock:
        # Convert deque to list for JSON serialization
        data_list = list(sensor_data)
    return jsonify(data_list)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
