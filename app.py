# app.py - Fixed version for Render
from flask import Flask, render_template, jsonify, request
import json
import time
import threading
import logging
from datetime import datetime
from dateutil.parser import parse
import pytz
import paho.mqtt.client as mqtt

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("plantbox")

app = Flask(__name__)

# Data storage - GLOBAL to ensure it's accessible across requests
GLOBAL_SENSOR_DATA = []
data_lock = threading.Lock()

# Configuration
TTN_APP_ID = "plant-data@ttn"
TTN_API_KEY = "NNSXS.SUFBQH4J3UNWANYVWQRGM4HQ6RRBKWUOJ5OBEKQ.DTGXKCCN7NG66NQYKM3VGUKY7JKQDFP3JC6ZBBKTCLSVQUZJGFQQ"
BROKER = "nam1.cloud.thethings.network"
PORT = 1883
TOPIC = f"v3/{TTN_APP_ID}/devices/+/up"

# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Connected to MQTT broker")
        client.subscribe(TOPIC)
    else:
        logger.error(f"Failed to connect with code {rc}")

def on_message(client, userdata, message):
    try:
        # Parse the message
        payload_str = message.payload.decode()
        js = json.loads(payload_str)
        
        # Log the receipt
        logger.info(f"Message received on topic: {message.topic}")
        
        # Extract data from payload
        ts = parse(js["received_at"]).astimezone(pytz.timezone("US/Eastern"))
        device_id = js["end_device_ids"]["device_id"]
        decoded = js["uplink_message"]["decoded_payload"]
        
        # Create record
        record = {
            "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "device_id": device_id,
            "temperature": decoded.get("boxTemperature"),
            "humidity": decoded.get("boxHumidity"),
            "plantheight": decoded.get("plantHeight"),
            "moisture1": decoded.get("moisture1"),
            "moisture2": decoded.get("moisture2"),
            "moisture3": decoded.get("moisture3")
        }
        
        # Add to global data - with lock for thread safety
        with data_lock:
            GLOBAL_SENSOR_DATA.insert(0, record)
            
            # Keep only the most recent 100 records
            if len(GLOBAL_SENSOR_DATA) > 100:
                GLOBAL_SENSOR_DATA.pop()
        
        logger.info(f"Data added: {record}")
        
    except Exception as e:
        logger.error(f"Error processing message: {e}")

# Start MQTT client in a non-blocking way
def start_mqtt_client():
    try:
        client = mqtt.Client("PlantBoxClient")
        client.username_pw_set(TTN_APP_ID, password=TTN_API_KEY)
        client.on_connect = on_connect
        client.on_message = on_message
        
        # Connect and start in non-blocking mode
        client.connect(BROKER, PORT, 60)
        client.loop_start()
        
        logger.info("MQTT client started")
        return client
    except Exception as e:
        logger.error(f"Error starting MQTT client: {e}")
        return None

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    with data_lock:
        # Make a copy to avoid thread issues
        data_copy = GLOBAL_SENSOR_DATA.copy()
    return jsonify(data_copy)

@app.route('/inject-test-data')
def inject_test_data():
    try:
        # Create test data
        record = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "device_id": "plant-box-featherboard",
            "temperature": 22.0,
            "humidity": 57.0,
            "plantheight": 7.7,
            "moisture1": 49.4,
            "moisture2": 94.7,
            "moisture3": 0.0
        }
        
        # Add to global data
        with data_lock:
            GLOBAL_SENSOR_DATA.insert(0, record)
        
        logger.info(f"Test data added: {record}")
        return jsonify({"success": True, "message": "Test data added"})
    except Exception as e:
        logger.error(f"Error adding test data: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route('/status')
def status():
    with data_lock:
        data_count = len(GLOBAL_SENSOR_DATA)
    
    return jsonify({
        'data_count': data_count,
        'server_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

# -----------------------------
# New function to add initial test data
# -----------------------------
def add_initial_test_data():
    record = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "device_id": "plant-box-featherboard",
        "temperature": 22.0,
        "humidity": 57.0,
        "plantheight": 7.7,
        "moisture1": 49.4,
        "moisture2": 94.7,
        "moisture3": 0.0
    }
    
    # Add to global data
    with data_lock:
        GLOBAL_SENSOR_DATA.insert(0, record)
    
    logger.info(f"Initial test data added: {record}")

# -----------------------------
# Initialization for Gunicorn
# -----------------------------
mqtt_client = None

# This function will be called when the app is initialized by Gunicorn
def initialize_app():
    global mqtt_client
    
    # Add test data
    add_initial_test_data()
    
    # Start MQTT client
    mqtt_client = start_mqtt_client()

# Only start when used with Gunicorn (not in development mode)
if __name__ != '__main__':
    initialize_app()

# For development mode
if __name__ == '__main__':
    # Add test data and start MQTT client
    add_initial_test_data()
    mqtt_client = start_mqtt_client()
    
    # Run Flask development server
    app.run(debug=True, host='0.0.0.0', port=5000)
