# app.py - Super simplified version
from flask import Flask, render_template, jsonify, request
import json
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

# Global variables
mqtt_client = None
mqtt_connected = False
file_lock = threading.Lock()

# File operations
def ensure_data_file():
    with file_lock:
        if not os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'w') as f:
                json.dump([], f)
            logger.info(f"Created data file: {DATA_FILE}")

def read_data_file():
    with file_lock:
        try:
            if not os.path.exists(DATA_FILE):
                return []
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading data file: {e}")
            return []

def write_data_file(data):
    with file_lock:
        try:
            with open(DATA_FILE, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"Error writing data file: {e}")

def add_data_record(record):
    with file_lock:
        data = read_data_file()
        data.insert(0, record)
        if len(data) > MAX_RECORDS:
            data = data[:MAX_RECORDS]
        write_data_file(data)

# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    global mqtt_connected
    if rc == 0:
        logger.info("Connected to MQTT broker")
        client.subscribe(TOPIC, qos=1)
        mqtt_connected = True
    else:
        logger.error(f"Connection failed with code {rc}")
        mqtt_connected = False

def on_message(client, userdata, message):
    try:
        # Parse the message
        payload = message.payload.decode()
        data = json.loads(payload)
        
        logger.info(f"Message received on topic: {message.topic}")
        
        # Extract data
        try:
            # Get timestamp
            ts = parse(data["received_at"]).astimezone(pytz.timezone("US/Eastern"))
            time_str = ts.strftime("%Y-%m-%d %H:%M:%S")
            
            # Get device ID
            device_id = data["end_device_ids"]["device_id"]
            
            # Get sensor data
            decoded = data["uplink_message"]["decoded_payload"]
            
            # Create record
            record = {
                "time": time_str,
                "device_id": device_id,
                "temperature": decoded.get("boxTemperature"),
                "humidity": decoded.get("boxHumidity"),
                "plantheight": decoded.get("plantHeight"),
                "moisture1": decoded.get("moisture1"),
                "moisture2": decoded.get("moisture2"),
                "moisture3": decoded.get("moisture3")
            }
            
            # Save to file
            add_data_record(record)
            
            logger.info(f"Data saved: {record}")
            
        except KeyError as e:
            logger.warning(f"Missing key in message: {e}")
            
    except Exception as e:
        logger.error(f"Error processing message: {e}")

# Start MQTT client
def start_mqtt():
    global mqtt_client
    
    # Create new client
    client = mqtt.Client("PlantBoxClient")
    client.username_pw_set(TTN_APP_ID, password=TTN_API_KEY)
    client.on_connect = on_connect
    client.on_message = on_message
    
    # Connect and start loop
    try:
        client.connect(BROKER, PORT, 60)
        client.loop_start()
        mqtt_client = client
        logger.info("MQTT client started")
    except Exception as e:
        logger.error(f"Error starting MQTT client: {e}")

# Add initial test data
def add_test_data():
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
    
    add_data_record(record)
    logger.info(f"Test data added: {record}")

# Initialize at module level
ensure_data_file()
if len(read_data_file()) == 0:
    add_test_data()

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    data = read_data_file()
    return jsonify(data)

@app.route('/inject-test-data')
def inject_test_data():
    try:
        add_test_data()
        return jsonify({"success": True, "message": "Test data added"})
    except Exception as e:
        logger.error(f"Error adding test data: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route('/status')
def status():
    data = read_data_file()
    return jsonify({
        "data_count": len(data),
        "mqtt_connected": mqtt_connected,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

# Start the MQTT client if not in main (for Gunicorn)
if __name__ != '__main__':
    # This won't call any Flask functions, so it's safe
    start_mqtt()
else:
    # For development mode
    start_mqtt()
    app.run(debug=True, host='0.0.0.0', port=5000)
