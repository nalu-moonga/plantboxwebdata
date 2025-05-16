# app.py - Simple file-based approach
from flask import Flask, render_template, jsonify, request
import json
import time
import logging
from datetime import datetime
import paho.mqtt.client as mqtt
from dateutil.parser import parse
import pytz
import os
import threading

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("plantbox")

app = Flask(__name__)

# Configuration
TTN_APP_ID = "plant-data@ttn"
TTN_API_KEY = "NNSXS.SUFBQH4J3UNWANYVWQRGM4HQ6RRBKWUOJ5OBEKQ.DTGXKCCN7NG66NQYKM3VGUKY7JKQDFP3JC6ZBBKTCLSVQUZJGFQQ"
BROKER = "nam1.cloud.thethings.network"
PORT = 1883
TOPIC = f"v3/{TTN_APP_ID}/devices/+/up"
DATA_FILE = "sensor_data.json"
MAX_RECORDS = 100

# Initialize data file if it doesn't exist
def init_data_file():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w') as f:
            json.dump([], f)
        logger.info(f"Created empty data file: {DATA_FILE}")

# Read data from file
def read_data():
    try:
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
        return data
    except Exception as e:
        logger.error(f"Error reading data file: {e}")
        return []

# Write data to file
def write_data(data):
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Error writing data file: {e}")

# Add a single record to data file
def add_record(record):
    data = read_data()
    data.insert(0, record)  # Add to beginning
    if len(data) > MAX_RECORDS:
        data = data[:MAX_RECORDS]  # Keep only MAX_RECORDS
    write_data(data)
    return data

# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info(f"Connected to MQTT broker: {BROKER}")
        client.subscribe(TOPIC, qos=1)
        logger.info(f"Subscribed to {TOPIC}")
    else:
        logger.error(f"Failed to connect to MQTT broker, code={rc}")

def on_message(client, userdata, message):
    try:
        # Parse the message
        payload_str = message.payload.decode('utf-8')
        js = json.loads(payload_str)
        
        logger.info(f"Message received on topic: {message.topic}")
        
        # Extract the data
        try:
            # Extract timestamp and convert to Eastern Time
            ts = parse(js["received_at"]).astimezone(pytz.timezone("US/Eastern"))
            formatted_time = ts.strftime("%Y-%m-%d %H:%M:%S")
            
            # Extract device ID
            device_id = js["end_device_ids"]["device_id"]
            
            # Extract sensor values
            decoded = js["uplink_message"]["decoded_payload"]
            
            # Create record
            record = {
                "time": formatted_time,
                "device_id": device_id,
                "temperature": decoded.get("boxTemperature"),
                "humidity": decoded.get("boxHumidity"),
                "plantheight": decoded.get("plantHeight"),
                "moisture1": decoded.get("moisture1"),
                "moisture2": decoded.get("moisture2"),
                "moisture3": decoded.get("moisture3")
            }
            
            # Add to file
            add_record(record)
            
            logger.info(f"Data saved: {record}")
            
        except KeyError as key_error:
            logger.warning(f"Missing key in message: {key_error}")
            logger.warning(f"Message keys: {list(js.keys())}")
            
    except Exception as e:
        logger.error(f"Error processing message: {e}")

# Start MQTT client
def start_mqtt_client():
    client = mqtt.Client("PlantBoxClient")
    client.username_pw_set(TTN_APP_ID, password=TTN_API_KEY)
    client.on_connect = on_connect
    client.on_message = on_message
    
    try:
        logger.info(f"Connecting to MQTT broker {BROKER}...")
        client.connect(BROKER, PORT, 60)
        client.loop_start()
        logger.info("MQTT client started")
        return client
    except Exception as e:
        logger.error(f"Error starting MQTT client: {e}")
        return None

# Flask routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    data = read_data()
    return jsonify(data)

@app.route('/inject-test-data')
def inject_test_data():
    try:
        # Create test data
        test_data = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "device_id": "plant-box-featherboard",
            "temperature": 22.0,
            "humidity": 57.0,
            "plantheight": 7.7,
            "moisture1": 49.4,
            "moisture2": 94.7,
            "moisture3": 0.0
        }
        
        # Add to file
        add_record(test_data)
        
        logger.info(f"Test data added: {test_data}")
        return jsonify({"success": True, "message": "Test data added"})
    except Exception as e:
        logger.error(f"Error adding test data: {e}")
        return jsonify({"error": str(e)})

@app.route('/status')
def status():
    data = read_data()
    
    return jsonify({
        'data_count': len(data),
        'server_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

# Initialize
init_data_file()

# Add initial test data
if not read_data():  # Only if empty
    inject_test_data()

# Start MQTT client
mqtt_client = None

# Only start in production mode
if __name__ != '__main__':
    mqtt_client = start_mqtt_client()

# For development mode
if __name__ == '__main__':
    mqtt_client = start_mqtt_client()
    app.run(debug=True, host='0.0.0.0', port=5000)
