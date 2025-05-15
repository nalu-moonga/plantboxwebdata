# app.py - Dual approach with MQTT and Webhook

import flask
from flask import Flask, render_template, jsonify, request
import paho.mqtt.client as mqtt
import json
import time
import threading
import logging
from datetime import datetime
from dateutil.parser import parse
import pytz
import collections
import os

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
MQTT_PORT = 1883
WS_PORT = 8883  # WebSockets port
TOPIC = f"v3/{TTN_APP_ID}/devices/+/up"

# Environment check - disable MQTT in certain environments
DISABLE_MQTT = os.environ.get('DISABLE_MQTT', 'false').lower() == 'true'

# Global variables
mqtt_connected = False
mqtt_client = None
last_raw_message = None
last_exception = None
received_message_count = 0
processed_message_count = 0
webhook_count = 0

# Data storage
MAX_RECORDS = 100
data_lock = threading.Lock()
sensor_data = collections.deque(maxlen=MAX_RECORDS)

# Common data processing function
def process_ttn_data(data_json, source="unknown"):
    global processed_message_count, webhook_count
    
    try:
        # Extract timestamp and convert to Eastern Time
        ts = parse(data_json["received_at"]).astimezone(pytz.timezone("US/Eastern"))
        formatted_time = ts.strftime("%Y-%m-%d %H:%M:%S")
        
        # Extract device ID
        device_id = data_json["end_device_ids"]["device_id"]
        
        # Extract sensor values
        decoded = data_json["uplink_message"]["decoded_payload"]
        record = {
            "time": formatted_time,
            "device_id": device_id,
            "temperature": decoded["boxTemperature"],
            "humidity": decoded["boxHumidity"],
            "plantheight": decoded["plantHeight"],
            "moisture1": decoded["moisture1"],
            "moisture2": decoded["moisture2"], 
            "moisture3": decoded["moisture3"],
            "source": source  # Track where this data came from
        }
        
        # Add to data collection
        with data_lock:
            sensor_data.appendleft(record)
        
        if source == "webhook":
            webhook_count += 1
        else:
            processed_message_count += 1
            
        logger.info(f"Processed data from {source}: {record}")
        return True, record
        
    except Exception as e:
        logger.error(f"Error processing data from {source}: {e}", exc_info=True)
        return False, str(e)

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
    global last_raw_message, last_exception, received_message_count
    
    received_message_count += 1
    logger.info(f"MQTT message #{received_message_count} received on topic: {message.topic}")
    
    try:
        # Store raw message for debugging
        payload_str = message.payload.decode('utf-8')
        last_raw_message = payload_str[:500] + "..." if len(payload_str) > 500 else payload_str
        
        # Parse JSON
        data_json = json.loads(payload_str)
        
        # Process using common function
        success, result = process_ttn_data(data_json, source="mqtt")
        if not success:
            logger.error(f"Failed to process MQTT message: {result}")
            
    except Exception as e:
        last_exception = e
        logger.error(f"Error handling MQTT message: {e}", exc_info=True)

# Setup MQTT with multiple connection options
def setup_mqtt_client(use_websockets=False):
    global mqtt_client
    
    # Create client with unique ID
    client_id = f"PlantBoxClient_{int(time.time())}"
    
    if use_websockets:
        client = mqtt.Client(client_id, transport="websockets")
        logger.info("Using WebSockets transport for MQTT")
    else:
        client = mqtt.Client(client_id)
        logger.info("Using standard TCP transport for MQTT")
    
    # Set callbacks
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.on_subscribe = on_subscribe
    
    # Enable logging
    client.enable_logger(logger)
    
    # Set credentials
    client.username_pw_set(TTN_APP_ID, password=TTN_API_KEY)
    
    # Store globally
    mqtt_client = client
    
    return client

# MQTT client thread
def mqtt_client_thread():
    global mqtt_connected
    
    # Exit if MQTT is disabled
    if DISABLE_MQTT:
        logger.info("MQTT client disabled by environment variable")
        return
    
    # Try WebSockets first, then standard TCP
    use_websockets = True
    
    while True:
        try:
            if not mqtt_connected:
                client = setup_mqtt_client(use_websockets=use_websockets)
                
                logger.info(f"Connecting to MQTT broker {BROKER} using "
                           f"{'WebSockets' if use_websockets else 'TCP'}...")
                
                # Connect with appropriate port
                port = WS_PORT if use_websockets else MQTT_PORT
                client.connect(BROKER, port, keepalive=60)
                
                # Start loop
                client.loop_start()
                
                # Wait for connection
                connection_timeout = 10
                start_time = time.time()
                while not mqtt_connected and time.time() - start_time < connection_timeout:
                    time.sleep(0.1)
                
                if not mqtt_connected:
                    logger.error("Connection failed, trying alternate transport")
                    client.loop_stop()
                    
                    # Try alternate transport method
                    use_websockets = not use_websockets
                    time.sleep(5)
                    continue
                
                logger.info(f"MQTT connected using {'WebSockets' if use_websockets else 'TCP'}")
            
            # Keep checking connection
            while True:
                time.sleep(5)
                
                if not mqtt_connected:
                    logger.warning("MQTT connection lost, reconnecting...")
                    client.loop_stop()
                    break
        
        except Exception as e:
            logger.error(f"MQTT error: {e}", exc_info=True)
            try:
                client.loop_stop()
            except:
                pass
            
            # Wait before retry
            time.sleep(10)

# Webhook endpoint
@app.route('/api/ttn-webhook', methods=['POST'])
def ttn_webhook():
    try:
        # Log request info
        logger.info(f"Webhook received: {request.method} {request.path}")
        
        # Get payload
        if not request.is_json:
            logger.warning("Webhook payload is not JSON")
            return jsonify({"error": "Expected JSON payload"}), 400
            
        payload = request.json
        
        # Process data
        success, result = process_ttn_data(payload, source="webhook")
        
        if success:
            return jsonify({"success": True}), 200
        else:
            return jsonify({"error": result}), 500
            
    except Exception as e:
        logger.error(f"Error handling webhook: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# Test data injection
@app.route('/inject-test-data')
def inject_test_data():
    test_json = """{"end_device_ids":{"device_id":"plant-box-featherboard","application_ids":{"application_id":"plant-data"},"dev_eui":"70B3D57ED0070888","join_eui":"0000000000000001","dev_addr":"260CB99E"},"correlation_ids":["gs:uplink:01JVAMV10QX88AQ4AZ1Q4NP0MP"],"received_at":"2025-05-15T18:55:49.481878360Z","uplink_message":{"session_key_id":"AZbOwJzRtWSKLK5kjiNc/A==","f_port":1,"f_cnt":3787,"frm_payload":"AACwQQAAZEK6EPdAYLxFQhlvvUIAAAAA","decoded_payload":{"boxHumidity":57,"boxTemperature":22,"moisture1":49.4339599609375,"moisture2":94.71698760986328,"moisture3":0,"plantHeight":7.720791816711426},"rx_metadata":[{"gateway_ids":{"gateway_id":"ttn-ithaca-00-08-00-4a-f0-89","eui":"00800000A000356F"},"time":"2025-05-15T18:55:49Z","timestamp":2184500988,"rssi":-121,"channel_rssi":-121,"snr":-12,"location":{"altitude":-1,"source":"SOURCE_REGISTRY"},"uplink_token":"CioKKAocdHRuLWl0aGFjYS0wMC0wOC0wMC00YS1mMC04ORIIAIAAAKAANW8Q/K3TkQgaDAi18ZjBBhCc3piBASDgkJ/zyZsM","received_at":"2025-05-15T18:55:49.270937884Z"}],"settings":{"data_rate":{"lora":{"bandwidth":125000,"spreading_factor":9,"coding_rate":"4/5"}},"frequency":"904300000","timestamp":2184500988},"received_at":"2025-05-15T18:55:49.272535519Z","consumed_airtime":"0.267264s","packet_error_rate":0.5263158,"network_ids":{"net_id":"000013","ns_id":"EC656E0000000182","tenant_id":"ttn","cluster_id":"nam1","cluster_address":"nam1.cloud.thethings.network"}}}"""
    
    try:
        data_json = json.loads(test_json)
        success, result = process_ttn_data(data_json, source="test_injection")
        
        if success:
            return jsonify({"success": True, "message": "Test data injected"}), 200
        else:
            return jsonify({"success": False, "error": result}), 500
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# Manual MQTT reconnect
@app.route('/reconnect-mqtt')
def reconnect_mqtt():
    global mqtt_client, mqtt_connected
    
    if DISABLE_MQTT:
        return jsonify({"success": False, "message": "MQTT is disabled"}), 200
    
    try:
        # Disconnect current client if it exists
        if mqtt_client:
            try:
                mqtt_client.loop_stop()
                mqtt_client.disconnect()
            except:
                pass
            
        # Reset connection flag
        mqtt_connected = False
        
        return jsonify({"success": True, "message": "MQTT reconnect initiated"}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# Toggle MQTT client
@app.route('/toggle-mqtt')
def toggle_mqtt():
    global DISABLE_MQTT
    
    DISABLE_MQTT = not DISABLE_MQTT
    
    if DISABLE_MQTT and mqtt_client:
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except:
            pass
    
    return jsonify({
        "success": True,
        "mqtt_disabled": DISABLE_MQTT,
        "message": f"MQTT client {'disabled' if DISABLE_MQTT else 'enabled'}"
    }), 200

# Debug endpoint
@app.route('/debug')
def debug():
    with data_lock:
        data_count = len(sensor_data)
        latest_data = list(sensor_data)[:5] if sensor_data else []
    
    # Check current MQTT client
    client_info = "Not created"
    if mqtt_client:
        client_info = f"Client ID: {mqtt_client._client_id.decode() if hasattr(mqtt_client, '_client_id') else 'unknown'}"
    
    debug_info = {
        'mqtt_disabled': DISABLE_MQTT,
        'mqtt_connected': mqtt_connected,
        'client_info': client_info,
        'data_count': data_count,
        'latest_data': latest_data,
        'last_raw_message': last_raw_message,
        'last_exception': str(last_exception) if last_exception else None,
        'server_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'topic': TOPIC,
        'received_messages': received_message_count,
        'processed_messages': processed_message_count,
        'webhook_count': webhook_count
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
    
    status_data = {
        'mqtt_disabled': DISABLE_MQTT,
        'mqtt_connected': mqtt_connected and not DISABLE_MQTT,
        'data_count': data_count,
        'received_messages': received_message_count,
        'processed_messages': processed_message_count,
        'webhook_count': webhook_count,
        'server_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    return jsonify(status_data)

# Start MQTT client thread
mqtt_thread = None

def start_mqtt_thread():
    global mqtt_thread
    if not DISABLE_MQTT and (mqtt_thread is None or not mqtt_thread.is_alive()):
        mqtt_thread = threading.Thread(target=mqtt_client_thread, daemon=True)
        mqtt_thread.start()
        logger.info("MQTT client thread started")
    elif DISABLE_MQTT:
        logger.info("MQTT client thread not started (disabled)")

# Start thread
start_mqtt_thread()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
else:
    # For production with Gunicorn
    # Start the background thread when loaded by Gunicorn
    start_mqtt_thread()
