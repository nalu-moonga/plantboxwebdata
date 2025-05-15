# app.py - Updated with direct JSON path access
from flask import Flask, render_template, jsonify
import paho.mqtt.client as mqtt
import json
import time
import threading
import logging
from datetime import datetime
from dateutil.parser import parse
import pytz
import collections

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('plantbox.log')
    ]
)
logger = logging.getLogger("plantbox")

app = Flask(__name__)

# Configuration
TTN_APP_ID = "plant-data@ttn"
TTN_API_KEY = "NNSXS.SUFBQH4J3UNWANYVWQRGM4HQ6RRBKWUOJ5OBEKQ.DTGXKCCN7NG66NQYKM3VGUKY7JKQDFP3JC6ZBBKTCLSVQUZJGFQQ"
BROKER = "nam1.cloud.thethings.network"
PORT = 1883
TOPIC = f"v3/{TTN_APP_ID}/devices/+/up"

# Global variables
mqtt_connected = False
last_raw_message = None
last_exception = None
received_message_count = 0
processed_message_count = 0

# Data storage
MAX_RECORDS = 100
data_lock = threading.Lock()
sensor_data = collections.deque(maxlen=MAX_RECORDS)

# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    global mqtt_connected
    if rc == 0:
        logger.info(f"Connected to MQTT broker: {BROKER}")
        result, mid = client.subscribe(TOPIC, qos=1)
        logger.info(f"Subscribed to {TOPIC}, result={result}, mid={mid}")
        mqtt_connected = True
    else:
        logger.error(f"Failed to connect to MQTT broker, code={rc}")
        mqtt_connected = False

def on_disconnect(client, userdata, rc):
    global mqtt_connected
    logger.warning(f"Disconnected from MQTT broker, code={rc}")
    mqtt_connected = False

def on_subscribe(client, userdata, mid, granted_qos):
    logger.info(f"Subscription confirmed, mid={mid}, qos={granted_qos}")

def on_message(client, userdata, message):
    global last_raw_message, last_exception, received_message_count, processed_message_count
    
    received_message_count += 1
    logger.info(f"MQTT message #{received_message_count} received on topic: {message.topic}")
    
    try:
        # Store raw message for debugging
        payload_str = message.payload.decode('utf-8')
        last_raw_message = payload_str[:500] + "..." if len(payload_str) > 500 else payload_str
        
        # Parse JSON
        js = json.loads(payload_str)
        
        # Log the full structure for debugging
        logger.info(f"Message keys: {list(js.keys())}")
        if "uplink_message" in js:
            logger.info(f"Uplink message keys: {list(js['uplink_message'].keys())}")
            if "decoded_payload" in js["uplink_message"]:
                logger.info(f"Decoded payload keys: {list(js['uplink_message']['decoded_payload'].keys())}")
        
        # Extract direct path values like your original script
        try:
            # Extract timestamp and convert to Eastern Time
            ts = parse(js["received_at"]).astimezone(pytz.timezone("US/Eastern"))
            formatted_time = ts.strftime("%Y-%m-%d %H:%M:%S")
            
            # Extract device ID
            device_id = js["end_device_ids"]["device_id"]
            
            # Extract sensor values directly using the path
            # This matches your original script approach
            record = {
                "time": formatted_time,
                "device_id": device_id,
                "temperature": js['uplink_message']['decoded_payload']['boxTemperature'],
                "humidity": js['uplink_message']['decoded_payload']['boxHumidity'],
                "plantheight": js['uplink_message']['decoded_payload']['plantHeight'],
                "moisture1": js['uplink_message']['decoded_payload']['moisture1'],
                "moisture2": js['uplink_message']['decoded_payload']['moisture2'],
                "moisture3": js['uplink_message']['decoded_payload']['moisture3'],
            }
            
            # Add to data collection
            with data_lock:
                sensor_data.appendleft(record)
            
            processed_message_count += 1
            logger.info(f"Processed data: {record}")
            
        except KeyError as key_error:
            # If direct path fails, log the specific missing key
            logger.warning(f"Missing key in message: {key_error}")
            logger.info(f"Message structure: {last_raw_message}")
            
            # Try a fallback approach with safer .get() method
            try:
                # Get the decoded payload
                decoded = js.get("uplink_message", {}).get("decoded_payload", {})
                
                # Create record with safer approach
                record = {
                    "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "device_id": js.get("end_device_ids", {}).get("device_id", "unknown"),
                    "temperature": decoded.get("boxTemperature"),
                    "humidity": decoded.get("boxHumidity"),
                    "plantheight": decoded.get("plantHeight"),
                    "moisture1": decoded.get("moisture1"),
                    "moisture2": decoded.get("moisture2"),
                    "moisture3": decoded.get("moisture3"),
                }
                
                # Add to data collection
                with data_lock:
                    sensor_data.appendleft(record)
                
                processed_message_count += 1
                logger.info(f"Processed data (fallback method): {record}")
            except Exception as fallback_error:
                # If fallback also fails, log that error
                logger.error(f"Fallback processing also failed: {fallback_error}")
        
    except Exception as e:
        last_exception = e
        logger.error(f"Error processing message: {e}", exc_info=True)
        logger.error(f"Raw payload: {message.payload}")

# MQTT client thread
def mqtt_client_thread():
    client = mqtt.Client("PlantBoxClient")
    client.username_pw_set(TTN_APP_ID, password=TTN_API_KEY)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.on_subscribe = on_subscribe
    
    while True:
        try:
            if not mqtt_connected:
                logger.info(f"Connecting to MQTT broker {BROKER}:{PORT}...")
                client.connect(BROKER, PORT, 60)
                
            client.loop_start()
            
            # Keep checking connection
            while True:
                time.sleep(5)
                if not mqtt_connected:
                    logger.warning("MQTT connection lost, reconnecting...")
                    try:
                        client.loop_stop()
                    except:
                        pass
                    break
                    
        except Exception as e:
            logger.error(f"MQTT error: {e}", exc_info=True)
            try:
                client.loop_stop()
            except:
                pass
            
            # Wait before retry
            logger.info("Waiting 10 seconds before reconnect...")
            time.sleep(10)

# Debug endpoint
@app.route('/debug')
def debug():
    with data_lock:
        data_count = len(sensor_data)
        latest_data = list(sensor_data)[:5] if sensor_data else []
    
    debug_info = {
        'mqtt_connected': mqtt_connected,
        'data_count': data_count,
        'latest_data': latest_data,
        'last_raw_message': last_raw_message,
        'last_exception': str(last_exception) if last_exception else None,
        'server_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'topic': TOPIC,
        'received_messages': received_message_count,
        'processed_messages': processed_message_count
    }
    
    return jsonify(debug_info)

# Standard routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    with data_lock:
        data_list = list(sensor_data)
    return jsonify(data_list)

# Status route
@app.route('/status')
def status():
    with data_lock:
        data_count = len(sensor_data)
    
    return jsonify({
        'mqtt_connected': mqtt_connected,
        'data_count': data_count,
        'received_messages': received_message_count,
        'processed_messages': processed_message_count,
        'server_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

# Start MQTT client thread
mqtt_thread = None

def start_mqtt_thread():
    global mqtt_thread
    if mqtt_thread is None or not mqtt_thread.is_alive():
        mqtt_thread = threading.Thread(target=mqtt_client_thread, daemon=True)
        mqtt_thread.start()
        logger.info("MQTT client thread started")

# Start thread
start_mqtt_thread()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
else:
    # For production with Gunicorn
    # Start the background thread when loaded by Gunicorn
    start_mqtt_thread()
