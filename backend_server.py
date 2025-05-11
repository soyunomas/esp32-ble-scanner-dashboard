import sqlite3
import logging
import json
from flask import Flask, request, jsonify, render_template
from datetime import datetime, date, timedelta #timedelta es NUEVO
import ble_utils
import math # Para math.ceil en el cálculo de total_pages
import pytz # Para manejo de zonas horarias
from collections import defaultdict # NUEVO para manufacturer_analysis

# --- Configuración ---
DATABASE_NAME = 'ble_data.db'
SERVER_HOST = '0.0.0.0'
SERVER_PORT = 5000
API_ENDPOINT_PATH = '/api/ble-data'

TARGET_TIMEZONE_PYTZ = pytz.timezone('Atlantic/Canary')
SQLITE_ANALYTICS_TIME_OFFSET = '+1 hours' 

app = Flask(__name__)

app.logger = logging.getLogger(__name__) 
# --- Configuración del Logging ---
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# File Handler
file_handler = logging.FileHandler("backend_server.log")
file_handler.setFormatter(log_formatter)
# Stream Handler (para consola)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)

# Usar el logger de Flask app
app.logger.addHandler(file_handler)
app.logger.addHandler(stream_handler)
app.logger.setLevel(logging.INFO)
app.logger.propagate = False


# --- Funciones de Base de Datos ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scanned_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                esp_device_id TEXT NOT NULL,
                ble_mac_address TEXT NOT NULL,
                ble_device_name TEXT,
                ble_rssi INTEGER,
                manufacturer_data TEXT,
                service_data TEXT,
                service_uuids TEXT,
                tx_power INTEGER,
                appearance INTEGER
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_mac_timestamp ON scanned_devices (ble_mac_address, timestamp DESC);')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_esp_id ON scanned_devices (esp_device_id);')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_mac_name_timestamp ON scanned_devices (ble_mac_address, ble_device_name, timestamp DESC);')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON scanned_devices (timestamp DESC);')
        # NUEVO: Índice para consultas de RSSI
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_mac_esp_timestamp_rssi ON scanned_devices (ble_mac_address, esp_device_id, timestamp, ble_rssi);')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_esp_timestamp_rssi ON scanned_devices (esp_device_id, timestamp, ble_rssi);')


        existing_columns_query = "PRAGMA table_info(scanned_devices);"
        cursor.execute(existing_columns_query)
        columns = [row['name'] for row in cursor.fetchall()]
        new_columns = {
            'manufacturer_data': 'TEXT', 'service_data': 'TEXT', 'service_uuids': 'TEXT',
            'tx_power': 'INTEGER', 'appearance': 'INTEGER'
        }
        for col_name, col_type in new_columns.items():
            if col_name not in columns:
                app.logger.info(f"Añadiendo columna '{col_name}' a 'scanned_devices'.")
                cursor.execute(f'ALTER TABLE scanned_devices ADD COLUMN {col_name} {col_type};')
        conn.commit()
        app.logger.info(f"Base de datos '{DATABASE_NAME}' inicializada y tabla 'scanned_devices' asegurada/actualizada.")
    except sqlite3.Error as e:
        app.logger.error(f"Error al inicializar/actualizar la base de datos: {e}")
    finally:
        if conn: conn.close()

# --- Helper para validar y convertir fechas ---
def validate_date_format(date_string):
    try:
        return datetime.strptime(date_string, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None

# --- Helper para convertir timestamp UTC string a local string ---
def convert_utc_to_local_string(utc_timestamp_str, target_tz):
    if not utc_timestamp_str:
        return None
    try:
        # Intentar parsear con o sin milisegundos
        try:
            naive_dt = datetime.strptime(utc_timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
        except ValueError:
            naive_dt = datetime.strptime(utc_timestamp_str, '%Y-%m-%d %H:%M:%S')
            
        utc_dt = pytz.utc.localize(naive_dt)
        local_dt = utc_dt.astimezone(target_tz)
        return local_dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        app.logger.error(f"Error convirtiendo timestamp '{utc_timestamp_str}' a local: {e}")
        return utc_timestamp_str 


# --- Endpoint de la API para recibir datos del ESP32 ---
@app.route(API_ENDPOINT_PATH, methods=['POST'])
def receive_ble_data():
    # ... (sin cambios en esta función) ...
    app.logger.info(f"Solicitud POST recibida en {API_ENDPOINT_PATH}")

    if not request.is_json:
        app.logger.warning("Solicitud rechazada: Content-Type no es application/json")
        return jsonify({"status": "error", "message": "Request Content-Type must be application/json"}), 415

    try:
        data = request.get_json()
        if not data:
            app.logger.warning("JSON vacío recibido.")
            return jsonify({"status": "error", "message": "Empty JSON payload"}), 400
    except Exception as e:
        app.logger.error(f"Error al parsear JSON: {e}")
        return jsonify({"status": "error", "message": "Invalid JSON format"}), 400

    esp_device_id = data.get('deviceId')
    devices_list = data.get('devices')

    if not esp_device_id:
        app.logger.warning("Falta el campo 'deviceId' en la solicitud.")
        return jsonify({"status": "error", "message": "Missing 'deviceId' field"}), 400
    
    if devices_list is None:
        app.logger.info(f"Recibido 'deviceId': {esp_device_id} con lista de 'devices' vacía o nula. Procesando solo ID.")
        devices_list = []
    elif not isinstance(devices_list, list):
        app.logger.warning("Campo 'devices' no es una lista.")
        return jsonify({"status": "error", "message": "'devices' field must be a list"}), 400
        
    devices_processed_count = 0
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        for device_data in devices_list:
            if not isinstance(device_data, dict):
                app.logger.warning(f"Elemento en 'devices' no es un diccionario: {device_data}")
                continue

            ble_mac_address = device_data.get('macAddress')
            ble_device_name = device_data.get('deviceName') 
            ble_rssi_raw = device_data.get('rssi')
            
            manufacturer_data = device_data.get('manufacturerData') 
            service_data_raw = device_data.get('serviceData') 
            service_uuids_raw = device_data.get('serviceUUIDs') 
            tx_power_raw = device_data.get('txPower')
            appearance_raw = device_data.get('appearance')

            if not ble_mac_address:
                app.logger.warning(f"Falta campo 'macAddress' en el objeto de dispositivo: {device_data} para ESP: {esp_device_id}")
                continue
            
            ble_rssi = None
            if ble_rssi_raw is not None:
                try:
                    ble_rssi = int(ble_rssi_raw)
                except (ValueError, TypeError):
                    app.logger.warning(f"Valor de 'rssi' no es un entero válido: {ble_rssi_raw} para MAC {ble_mac_address}. ESP: {esp_device_id}")

            tx_power = None
            if tx_power_raw is not None:
                try:
                    tx_power = int(tx_power_raw)
                except (ValueError, TypeError):
                    app.logger.warning(f"Valor de 'txPower' no es un entero válido: {tx_power_raw} para MAC {ble_mac_address}.")

            appearance = None
            if appearance_raw is not None:
                try:
                    appearance = int(appearance_raw)
                except (ValueError, TypeError):
                    app.logger.warning(f"Valor de 'appearance' no es un entero válido: {appearance_raw} para MAC {ble_mac_address}.")

            service_data_json_str = None
            if service_data_raw: 
                if isinstance(service_data_raw, dict):
                    service_data_json_str = json.dumps(service_data_raw)
                elif isinstance(service_data_raw, str): 
                     try:
                        json.loads(service_data_raw) 
                        service_data_json_str = service_data_raw
                     except json.JSONDecodeError:
                        app.logger.warning(f"service_data_raw no es un JSON string válido: {service_data_raw}")
            
            service_uuids_json_str = None
            if service_uuids_raw: 
                if isinstance(service_uuids_raw, list):
                    service_uuids_json_str = json.dumps(service_uuids_raw)
                elif isinstance(service_uuids_raw, str): 
                    try:
                        json.loads(service_uuids_raw) 
                        service_uuids_json_str = service_uuids_raw
                    except json.JSONDecodeError:
                        app.logger.warning(f"service_uuids_raw no es un JSON string válido: {service_uuids_raw}")

            sql = """
                INSERT INTO scanned_devices (
                    esp_device_id, ble_mac_address, ble_device_name, ble_rssi,
                    manufacturer_data, service_data, service_uuids, tx_power, appearance
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            cursor.execute(sql, (
                esp_device_id, ble_mac_address, ble_device_name, ble_rssi,
                manufacturer_data, service_data_json_str, service_uuids_json_str,
                tx_power, appearance
            ))
            devices_processed_count += 1
        
        conn.commit()
        if devices_list:
            app.logger.info(f"Datos de {devices_processed_count} dispositivos BLE almacenados correctamente para ESP: {esp_device_id}.")
        else:
            app.logger.info(f"Recibido ping/heartbeat de ESP: {esp_device_id} (sin dispositivos BLE en payload).")

    except sqlite3.Error as e:
        app.logger.error(f"Error de base de datos al insertar datos: {e}")
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": "Database error occurred during insert"}), 500
    except Exception as e:
        app.logger.error(f"Error inesperado al procesar dispositivos: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "An unexpected error occurred"}), 500
    finally:
        if conn:
            conn.close()

    return jsonify({
        "status": "success",
        "message": "Data received and processed.",
        "devices_processed": devices_processed_count
    }), 201


# --- Endpoint para el Dashboard Principal ---
@app.route('/dashboard')
def dashboard():
    # ... (sin cambios aquí) ...
    app.logger.info("Solicitud GET recibida en /dashboard")
    conn = None
    try:
        conn = get_db_connection()
        esp_device_counts_rows = conn.execute(
            'SELECT esp_device_id, COUNT(DISTINCT ble_mac_address) as unique_device_count FROM scanned_devices GROUP BY esp_device_id ORDER BY esp_device_id'
        ).fetchall()
        esp_chart_labels = [row['esp_device_id'] for row in esp_device_counts_rows]
        esp_chart_data = [row['unique_device_count'] for row in esp_device_counts_rows]

        rssi_distribution_rows = conn.execute('''
            SELECT
                CASE
                    WHEN ble_rssi >= -50 THEN '-50 a 0 dBm'
                    WHEN ble_rssi >= -60 THEN '-60 a -51 dBm'
                    WHEN ble_rssi >= -70 THEN '-70 a -61 dBm'
                    WHEN ble_rssi >= -80 THEN '-80 a -71 dBm'
                    WHEN ble_rssi >= -90 THEN '-90 a -81 dBm'
                    ELSE '< -90 dBm'
                END as rssi_range,
                COUNT(*) as count
            FROM scanned_devices WHERE ble_rssi IS NOT NULL
            GROUP BY rssi_range
            ORDER BY MIN(ble_rssi) DESC
        ''').fetchall()
        rssi_chart_labels = [row['rssi_range'] for row in rssi_distribution_rows]
        rssi_chart_data = [row['count'] for row in rssi_distribution_rows]

        return render_template('dashboard.html',
                               esp_chart_labels=esp_chart_labels,
                               esp_chart_data=esp_chart_data,
                               rssi_chart_labels=rssi_chart_labels,
                               rssi_chart_data=rssi_chart_data)
    except sqlite3.Error as e:
        app.logger.error(f"Error de base de datos al cargar datos para gráficas del dashboard: {e}")
        return render_template('dashboard.html', esp_chart_labels=[], esp_chart_data=[], rssi_chart_labels=[], rssi_chart_data=[]), 500
    except Exception as e:
        app.logger.error(f"Error inesperado al cargar el dashboard: {e}", exc_info=True)
        return render_template('dashboard.html', esp_chart_labels=[], esp_chart_data=[], rssi_chart_labels=[], rssi_chart_data=[]), 500
    finally:
        if conn: conn.close()

# --- ENDPOINT API PARA LA TABLA DE DISPOSITIVOS ÚNICOS PAGINADA ---
@app.route('/api/unique-devices')
def get_unique_devices_paginated():
    # ... (sin cambios en esta función) ...
    app.logger.info("Solicitud GET recibida en /api/unique-devices")
    try:
        page = request.args.get('page', 1, type=int)
        page_size_req = request.args.get('page_size', 20, type=int)
        sort_by_param = request.args.get('sort_by', 'last_seen_timestamp').strip()
        sort_order_param = request.args.get('sort_order', 'desc').strip().lower()

        if page < 1: page = 1
        valid_page_sizes = [20, 30, 40, 50, 60, 70, 80, 90, 100]
        page_size = page_size_req if page_size_req in valid_page_sizes else 20
        
        valid_sort_columns_map = {
            'last_seen_timestamp': 's_grouped.max_timestamp_utc', 
            'ble_mac_address': 's_grouped.ble_mac_address',
            'best_ble_device_name': 'best_ble_device_name_alias', 
            'manufacturer_name': 'last_manufacturer_data',
            'adv_packets_count': 's_grouped.adv_packets_count'
        }

        if sort_by_param not in valid_sort_columns_map:
            app.logger.warning(f"Parámetro sort_by inválido: {sort_by_param}. Usando 'last_seen_timestamp'.")
            sort_by_param = 'last_seen_timestamp'
        
        db_sort_column_expression = valid_sort_columns_map[sort_by_param]

        if sort_order_param not in ['asc', 'desc']:
            sort_order_param = 'desc'

        offset = (page - 1) * page_size
        conn = get_db_connection()
        
        total_devices_query = "SELECT COUNT(DISTINCT ble_mac_address) as total FROM scanned_devices;"
        total_devices_result = conn.execute(total_devices_query).fetchone()
        total_devices = total_devices_result['total'] if total_devices_result else 0
        total_pages = math.ceil(total_devices / page_size) if page_size > 0 and total_devices > 0 else 1 if total_devices == 0 else math.ceil(total_devices / page_size)
        
        if page > total_pages and total_pages > 0 :
             page = total_pages 
             offset = (page - 1) * page_size

        sub_query_grouped = """
            SELECT 
                ble_mac_address, 
                MAX(timestamp) as max_timestamp_utc, 
                COUNT(*) as adv_packets_count 
            FROM scanned_devices
            GROUP BY ble_mac_address
        """
        
        query = f"""
            SELECT
                s_grouped.ble_mac_address,
                s_grouped.max_timestamp_utc, 
                s_grouped.adv_packets_count, 
                COALESCE(
                    (SELECT s_name.ble_device_name 
                     FROM scanned_devices s_name
                     WHERE s_name.ble_mac_address = s_grouped.ble_mac_address 
                       AND s_name.ble_device_name IS NOT NULL AND s_name.ble_device_name != ''
                     ORDER BY s_name.timestamp DESC, s_name.id DESC LIMIT 1), 'N/A'
                ) as best_ble_device_name_alias,
                (SELECT s_data.manufacturer_data
                 FROM scanned_devices s_data
                 WHERE s_data.ble_mac_address = s_grouped.ble_mac_address
                 ORDER BY s_data.timestamp DESC, s_data.id DESC LIMIT 1
                ) as last_manufacturer_data
            FROM ({sub_query_grouped}) s_grouped
            ORDER BY {db_sort_column_expression} {sort_order_param}, s_grouped.ble_mac_address {sort_order_param}
            LIMIT ? OFFSET ?
        """
        
        app.logger.debug(f"Executing query for unique devices: {query} with params: ({page_size}, {offset})")
        raw_unique_devices = conn.execute(query, (page_size, offset)).fetchall()
        
        unique_devices_processed = []
        for dev_row_raw in raw_unique_devices:
            dev_dict = dict(dev_row_raw)
            utc_ts_str = dev_dict.pop('max_timestamp_utc') 
            dev_dict['last_seen_timestamp'] = convert_utc_to_local_string(utc_ts_str, TARGET_TIMEZONE_PYTZ)

            manufacturer_name_str = "N/A"
            if dev_dict.get('last_manufacturer_data'):
                name, _, _ = ble_utils.parse_manufacturer_data(dev_dict['last_manufacturer_data'])
                manufacturer_name_str = name if name != "Unknown CID" else f"Unknown ({dev_dict['last_manufacturer_data'][:4]})"
            dev_dict['manufacturer_name'] = manufacturer_name_str
            dev_dict['best_ble_device_name'] = dev_dict.pop('best_ble_device_name_alias', 'N/A')
            unique_devices_processed.append(dev_dict)

        conn.close()

        app.logger.info(f"Devolviendo {len(unique_devices_processed)} dispositivos únicos para la página {page} de {total_pages} (tamaño {page_size}). Total: {total_devices}.")
        return jsonify({
            "devices": unique_devices_processed,
            "total_devices": total_devices, "current_page": page, "page_size": page_size,
            "total_pages": total_pages, "sort_by": sort_by_param, "sort_order": sort_order_param
        })

    except sqlite3.Error as e:
        app.logger.error(f"Error de base de datos en /api/unique-devices: {e}", exc_info=True)
        return jsonify({"error": "Database error occurred"}), 500
    except Exception as e:
        app.logger.error(f"Error inesperado en /api/unique-devices: {e}", exc_info=True)
        return jsonify({"error": "Unexpected server error"}), 500


# --- Endpoint API para obtener el historial de un dispositivo específico ---
@app.route('/api/device-history/<mac_address>')
def device_history(mac_address):
    # ... (sin cambios en esta función) ...
    app.logger.info(f"Solicitud GET para historial del dispositivo MAC: {mac_address}")
    conn = None
    try:
        conn = get_db_connection()
        history_query = """
            SELECT 
                id, 
                timestamp as timestamp_utc, 
                esp_device_id, ble_device_name, ble_rssi,
                manufacturer_data, service_data, service_uuids,
                tx_power, appearance
            FROM scanned_devices
            WHERE ble_mac_address = ?
            ORDER BY timestamp DESC LIMIT 20; 
        """
        device_logs_raw = conn.execute(history_query, (mac_address,)).fetchall()
        
        logs_list_processed = []
        for row_raw in device_logs_raw:
            log_dict = dict(row_raw)
            utc_ts_str = log_dict.pop('timestamp_utc')
            log_dict['formatted_timestamp'] = convert_utc_to_local_string(utc_ts_str, TARGET_TIMEZONE_PYTZ)
            
            company_name, specific_data, company_id_hex = "N/A", "N/A", "N/A"
            if log_dict.get('manufacturer_data'):
                company_name, specific_data, company_id_hex = ble_utils.parse_manufacturer_data(log_dict['manufacturer_data'])
            log_dict['manufacturer_company'] = company_name
            log_dict['manufacturer_specific_data'] = specific_data
            log_dict['manufacturer_company_id'] = company_id_hex

            log_dict['service_uuids_resolved'] = []
            if log_dict.get('service_uuids'):
                try:
                    service_uuids_list = json.loads(log_dict['service_uuids'])
                    if isinstance(service_uuids_list, list):
                        for uuid_str in service_uuids_list:
                            name = ble_utils.SERVICE_UUIDS_NAMES.get(uuid_str.lower(), "Unknown Service")
                            log_dict['service_uuids_resolved'].append({"uuid": uuid_str, "name": name})
                    else: 
                        log_dict['service_uuids_resolved'].append({"uuid": str(service_uuids_list), "name": "Invalid format in DB (not a list)"})
                except json.JSONDecodeError:
                    app.logger.warning(f"Error decodificando service_uuids JSON de la BD para MAC {mac_address}, ID {log_dict['id']}: {log_dict['service_uuids']}")
                    log_dict['service_uuids_resolved'] = [{"uuid": log_dict['service_uuids'], "name": "Error parsing UUID JSON list"}]
            
            log_dict['service_data_resolved'] = {}
            if log_dict.get('service_data'):
                try:
                    service_data_obj = json.loads(log_dict['service_data'])
                    if isinstance(service_data_obj, dict):
                        for uuid_key, data_value in service_data_obj.items():
                            service_name = ble_utils.SERVICE_UUIDS_NAMES.get(uuid_key.lower(), uuid_key) 
                            resolved_key = f"{service_name} ({uuid_key})" 
                            log_dict['service_data_resolved'][resolved_key] = data_value
                    else: 
                         log_dict['service_data_resolved'] = {"error": "Service Data in DB not a valid JSON object"}
                except json.JSONDecodeError:
                    app.logger.warning(f"Error decodificando service_data JSON de la BD para MAC {mac_address}, ID {log_dict['id']}: {log_dict['service_data']}")
                    log_dict['service_data_resolved'] = {"error": f"Could not parse Service Data JSON: {log_dict['service_data']}"}
            
            logs_list_processed.append(log_dict)
            
        return jsonify(logs_list_processed)
    except sqlite3.Error as e:
        app.logger.error(f"Error de base de datos obteniendo historial para {mac_address}: {e}")
        return jsonify({"error": "Database error while fetching history"}), 500
    except Exception as e:
        app.logger.error(f"Error inesperado obteniendo historial para {mac_address}: {e}", exc_info=True)
        return jsonify({"error": "Unexpected server error while fetching history"}), 500
    finally:
        if conn: conn.close()


# --- ENDPOINTS PARA ANÁLISIS AVANZADO ---
@app.route('/api/device-activity/<mac_address>')
def device_activity_analysis(mac_address):
    # ... (sin cambios en esta función) ...
    app.logger.info(f"Solicitud GET para análisis de actividad del dispositivo MAC: {mac_address}")
    
    granularity = request.args.get('granularity', 'hourly') 
    start_date_str = request.args.get('startDate')
    end_date_str = request.args.get('endDate')

    valid_granularities = ['hourly', 'daily_week', 'weekly', 'monthly', 'daily_date']
    if granularity not in valid_granularities:
        return jsonify({"error": "Invalid granularity parameter"}), 400
    if not mac_address or len(mac_address) != 17: 
         return jsonify({"error": "Invalid MAC address format"}), 400

    params = [mac_address]
    date_filters = []
    start_date_obj = None
    end_date_obj = None

    if start_date_str:
        start_date_obj = validate_date_format(start_date_str)
        if not start_date_obj: return jsonify({"error": "Invalid startDate format. Use YYYY-MM-DD."}), 400
        date_filters.append(f"date(datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) >= date(?)")
        params.append(start_date_obj.strftime('%Y-%m-%d'))

    if end_date_str:
        end_date_obj = validate_date_format(end_date_str)
        if not end_date_obj: return jsonify({"error": "Invalid endDate format. Use YYYY-MM-DD."}), 400
        date_filters.append(f"date(datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) <= date(?)")
        params.append(end_date_obj.strftime('%Y-%m-%d'))
    
    if granularity == 'daily_date' and (not start_date_obj or not end_date_obj):
        return jsonify({"error": "startDate and endDate are required for daily_date granularity."}), 400
    if start_date_obj and end_date_obj and start_date_obj > end_date_obj:
        return jsonify({"error": "startDate cannot be after endDate."}), 400

    date_filter_sql = ""
    if date_filters: date_filter_sql = "AND " + " AND ".join(date_filters)

    group_by_sql = ""
    select_label_sql = ""
    order_by_sql = ""

    if granularity == 'hourly':
        select_label_sql = f"strftime('%H', datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) as time_group"
        group_by_sql = f"strftime('%H', datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}'))"
        order_by_sql = "time_group ASC"
    elif granularity == 'daily_week':
        select_label_sql = f"strftime('%w', datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) as time_group"
        group_by_sql = f"strftime('%w', datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}'))"
        order_by_sql = "time_group ASC" 
    elif granularity == 'weekly':
        select_label_sql = f"strftime('%Y-W%W', datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) as time_group"
        group_by_sql = f"strftime('%Y-W%W', datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}'))"
        order_by_sql = "time_group ASC"
    elif granularity == 'monthly':
        select_label_sql = f"strftime('%Y-%m', datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) as time_group"
        group_by_sql = f"strftime('%Y-%m', datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}'))"
        order_by_sql = "time_group ASC"
    elif granularity == 'daily_date':
        select_label_sql = f"strftime('%Y-%m-%d', datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) as time_group"
        group_by_sql = f"strftime('%Y-%m-%d', datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}'))"
        order_by_sql = "time_group ASC"

    query = f"""
        SELECT {select_label_sql}, COUNT(*) as count
        FROM scanned_devices
        WHERE ble_mac_address = ? {date_filter_sql}
        GROUP BY {group_by_sql}
        ORDER BY {order_by_sql};
    """
    
    conn = None
    try:
        conn = get_db_connection()
        app.logger.debug(f"Ejecutando query para device-activity ({granularity}): {query} con params: {params}")
        results_raw = conn.execute(query, tuple(params)).fetchall()
        
        labels = []
        data_counts = []

        if granularity == 'daily_week':
            sqlite_day_to_name_map = {'0': 'Domingo', '1': 'Lunes', '2': 'Martes', '3': 'Miércoles', '4': 'Jueves', '5': 'Viernes', '6': 'Sábado'}
            ordered_day_names = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
            activity_by_day = {name: 0 for name in ordered_day_names}
            for row in results_raw:
                sqlite_day_index = row['time_group'] 
                day_name_from_db = sqlite_day_to_name_map.get(sqlite_day_index)
                if day_name_from_db in activity_by_day:
                    activity_by_day[day_name_from_db] = row['count']
            labels = ordered_day_names
            data_counts = [activity_by_day[name] for name in ordered_day_names]
        elif granularity == 'hourly':
            hourly_counts = {f"{h:02d}":0 for h in range(24)}
            for row in results_raw:
                hourly_counts[row['time_group']] = row['count']
            for hour_str in sorted(hourly_counts.keys()):
                labels.append(f"{hour_str}:00")
                data_counts.append(hourly_counts[hour_str])
        elif granularity == 'daily_date':
            activity_by_date = {row['time_group']: row['count'] for row in results_raw}
            current_date = start_date_obj
            while current_date <= end_date_obj:
                date_str = current_date.strftime('%Y-%m-%d')
                labels.append(date_str)
                data_counts.append(activity_by_date.get(date_str, 0))
                current_date += timedelta(days=1)
        else: # weekly, monthly
            for row in results_raw:
                labels.append(row['time_group'])
                data_counts.append(row['count'])
        
        if not results_raw and granularity not in ['hourly', 'daily_week', 'daily_date']:
            app.logger.info(f"No se encontraron datos de actividad para MAC {mac_address} con los filtros aplicados ({granularity}).")
        
        return jsonify({"labels": labels, "data": data_counts})

    except sqlite3.Error as e:
        app.logger.error(f"Error de BD en device_activity_analysis para {mac_address}: {e}")
        return jsonify({"error": "Database error occurred"}), 500
    except Exception as e:
        app.logger.error(f"Error inesperado en device_activity_analysis para {mac_address}: {e}", exc_info=True)
        return jsonify({"error": "Unexpected server error"}), 500
    finally:
        if conn: conn.close()


@app.route('/api/peak-activity-hours')
def peak_activity_hours_analysis():
    # ... (sin cambios en esta función) ...
    app.logger.info("Solicitud GET para análisis de horas pico de actividad.")
    
    start_date_str = request.args.get('startDate')
    end_date_str = request.args.get('endDate')

    if not start_date_str or not end_date_str:
        return jsonify({"error": "startDate and endDate parameters are required."}), 400

    start_date_obj = validate_date_format(start_date_str)
    if not start_date_obj: return jsonify({"error": "Invalid startDate format. Use YYYY-MM-DD."}), 400
    
    end_date_obj = validate_date_format(end_date_str)
    if not end_date_obj: return jsonify({"error": "Invalid endDate format. Use YYYY-MM-DD."}), 400

    if start_date_obj > end_date_obj: return jsonify({"error": "startDate cannot be after endDate."}), 400

    params = [start_date_obj.strftime('%Y-%m-%d'), end_date_obj.strftime('%Y-%m-%d')]
    
    query = f"""
        SELECT 
            strftime('%H', datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) as hour_of_day,
            COUNT(DISTINCT ble_mac_address) as unique_device_count
        FROM scanned_devices
        WHERE date(datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) BETWEEN date(?) AND date(?)
        GROUP BY strftime('%H', datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}'))
        ORDER BY hour_of_day ASC; 
    """ 

    conn = None
    try:
        conn = get_db_connection()
        app.logger.debug(f"Ejecutando query para peak-activity-hours: {query} con params: {params}")
        results = conn.execute(query, tuple(params)).fetchall()

        hourly_counts = {f"{h:02d}":0 for h in range(24)}
        for row in results:
            hourly_counts[row['hour_of_day']] = row['unique_device_count']
            
        labels = [f"{hour_str}:00" for hour_str in sorted(hourly_counts.keys())]
        data_counts = [hourly_counts[hour_str] for hour_str in sorted(hourly_counts.keys())]
        
        if not any(dc > 0 for dc in data_counts):
            app.logger.info(f"No se encontraron datos de actividad pico para el rango {start_date_str} a {end_date_str}.")
        
        return jsonify({"labels": labels, "data": data_counts})

    except sqlite3.Error as e:
        app.logger.error(f"Error de base de datos en peak_activity_hours_analysis: {e}")
        return jsonify({"error": "Database error occurred"}), 500
    except Exception as e:
        app.logger.error(f"Error inesperado en peak_activity_hours_analysis: {e}", exc_info=True)
        return jsonify({"error": "Unexpected server error"}), 500
    finally:
        if conn: conn.close()


@app.route('/api/manufacturer-analysis')
def manufacturer_analysis():
    # ... (sin cambios en esta función) ...
    app.logger.info("Solicitud GET para análisis de fabricantes.")
    top_n_str = request.args.get('topN', '7') 
    start_date_str = request.args.get('startDate')
    end_date_str = request.args.get('endDate')

    try:
        top_n = int(top_n_str)
        if top_n <= 0: top_n = 7
    except ValueError:
        top_n = 7
        app.logger.warning(f"Valor de topN inválido '{top_n_str}', usando default {top_n}.")

    date_filters_sql_parts = []
    query_params = []

    if start_date_str:
        start_date_obj = validate_date_format(start_date_str)
        if not start_date_obj: return jsonify({"error": "Invalid startDate format. Use YYYY-MM-DD."}), 400
        date_filters_sql_parts.append(f"date(datetime(s_data.timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) >= date(?)")
        query_params.append(start_date_obj.strftime('%Y-%m-%d'))

    if end_date_str:
        end_date_obj = validate_date_format(end_date_str)
        if not end_date_obj: return jsonify({"error": "Invalid endDate format. Use YYYY-MM-DD."}), 400
        date_filters_sql_parts.append(f"date(datetime(s_data.timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) <= date(?)")
        query_params.append(end_date_obj.strftime('%Y-%m-%d'))
    
    date_filter_subquery_sql = ""
    if date_filters_sql_parts:
        date_filter_subquery_sql = "AND " + " AND ".join(date_filters_sql_parts)

    query_latest_mfg_per_mac = f"""
        SELECT
            s_outer.ble_mac_address,
            (SELECT s_data.manufacturer_data
             FROM scanned_devices s_data
             WHERE s_data.ble_mac_address = s_outer.ble_mac_address
               AND s_data.manufacturer_data IS NOT NULL AND s_data.manufacturer_data != ''
               {date_filter_subquery_sql} 
             ORDER BY s_data.timestamp DESC, s_data.id DESC LIMIT 1
            ) as last_manufacturer_data
        FROM scanned_devices s_outer
        WHERE 1=1 {'AND ' + ' AND '.join(d.replace('s_data.timestamp', 's_outer.timestamp') for d in date_filters_sql_parts) if date_filters_sql_parts else ''}
        GROUP BY s_outer.ble_mac_address
        HAVING last_manufacturer_data IS NOT NULL;
    """
    final_query_params = query_params + query_params if date_filters_sql_parts else []

    conn = None
    try:
        conn = get_db_connection()
        app.logger.debug(f"Ejecutando query para manufacturer_analysis (fase 1 - datos crudos): {query_latest_mfg_per_mac} con params: {final_query_params}")
        
        raw_data_for_parsing = conn.execute(query_latest_mfg_per_mac, tuple(final_query_params)).fetchall()

        manufacturer_counts = defaultdict(int)
        for row in raw_data_for_parsing:
            mfg_hex = row['last_manufacturer_data']
            name, _, _ = ble_utils.parse_manufacturer_data(mfg_hex)
            
            if name == "Unknown CID" or "N/A" in name:
                display_name = "Desconocido/Otro"
            else:
                display_name = name
            manufacturer_counts[display_name] += 1
        
        sorted_manufacturers = sorted(manufacturer_counts.items(), key=lambda item: item[1], reverse=True)
        
        labels = []
        data_counts = []
        
        if not sorted_manufacturers:
            app.logger.info("No se encontraron datos de fabricantes para los filtros aplicados.")
        else:
            for i, (name, count) in enumerate(sorted_manufacturers):
                if i < top_n:
                    labels.append(name)
                    data_counts.append(count)
                elif i == top_n: 
                    labels.append("Otros")
                    data_counts.append(count)
                else: 
                    data_counts[-1] += count
        
        return jsonify({"labels": labels, "data": data_counts})

    except sqlite3.Error as e:
        app.logger.error(f"Error de BD en manufacturer_analysis: {e}", exc_info=True)
        return jsonify({"error": "Database error occurred"}), 500
    except Exception as e:
        app.logger.error(f"Error inesperado en manufacturer_analysis: {e}", exc_info=True)
        return jsonify({"error": "Unexpected server error"}), 500
    finally:
        if conn: conn.close()


# --- NUEVOS ENDPOINTS PARA ANÁLISIS RSSI ---
@app.route('/api/all-known-esps')
def get_all_known_esps():
    app.logger.info("Solicitud GET para /api/all-known-esps")
    conn = None
    try:
        conn = get_db_connection()
        esps = conn.execute("SELECT DISTINCT esp_device_id FROM scanned_devices ORDER BY esp_device_id ASC").fetchall()
        return jsonify([row['esp_device_id'] for row in esps])
    except sqlite3.Error as e:
        app.logger.error(f"Error de BD en /api/all-known-esps: {e}")
        return jsonify({"error": "Database error"}), 500
    finally:
        if conn: conn.close()

@app.route('/api/esps-for-mac/<mac_address>')
def get_esps_for_mac(mac_address):
    app.logger.info(f"Solicitud GET para /api/esps-for-mac/{mac_address}")
    if not mac_address or len(mac_address) != 17:
        return jsonify({"error": "Invalid MAC address format"}), 400
    conn = None
    try:
        conn = get_db_connection()
        esps = conn.execute(
            "SELECT DISTINCT esp_device_id FROM scanned_devices WHERE ble_mac_address = ? ORDER BY esp_device_id ASC",
            (mac_address,)
        ).fetchall()
        return jsonify([row['esp_device_id'] for row in esps])
    except sqlite3.Error as e:
        app.logger.error(f"Error de BD en /api/esps-for-mac/{mac_address}: {e}")
        return jsonify({"error": "Database error"}), 500
    finally:
        if conn: conn.close()


@app.route('/api/device-rssi-trend/<mac_address>')
def device_rssi_trend(mac_address):
    app.logger.info(f"Solicitud GET para /api/device-rssi-trend/{mac_address}")
    if not mac_address or len(mac_address) != 17:
        return jsonify({"error": "Invalid MAC address format"}), 400

    start_date_str = request.args.get('startDate')
    end_date_str = request.args.get('endDate')
    filter_esp_id = request.args.get('esp_id') # Puede estar vacío o no presente

    params = [mac_address]
    sql_conditions = ["s.ble_mac_address = ?", "s.ble_rssi IS NOT NULL"]

    start_date_obj, end_date_obj = None, None
    if start_date_str:
        start_date_obj = validate_date_format(start_date_str)
        if not start_date_obj: return jsonify({"error": "Invalid startDate format. Use YYYY-MM-DD."}), 400
        sql_conditions.append(f"date(datetime(s.timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) >= date(?)")
        params.append(start_date_obj.strftime('%Y-%m-%d'))

    if end_date_str:
        end_date_obj = validate_date_format(end_date_str)
        if not end_date_obj: return jsonify({"error": "Invalid endDate format. Use YYYY-MM-DD."}), 400
        sql_conditions.append(f"date(datetime(s.timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) <= date(?)")
        params.append(end_date_obj.strftime('%Y-%m-%d'))
    
    if start_date_obj and end_date_obj and start_date_obj > end_date_obj:
        return jsonify({"error": "startDate cannot be after endDate."}), 400

    if filter_esp_id:
        sql_conditions.append("s.esp_device_id = ?")
        params.append(filter_esp_id)

    query = f"""
        SELECT s.timestamp as timestamp_utc, s.ble_rssi, s.esp_device_id
        FROM scanned_devices s
        WHERE {' AND '.join(sql_conditions)}
        ORDER BY s.timestamp ASC
        LIMIT 1000;
    """ # Limitado a 1000 puntos para rendimiento del gráfico

    conn = None
    try:
        conn = get_db_connection()
        app.logger.debug(f"Executing query for device-rssi-trend: {query} with params: {params}")
        results_raw = conn.execute(query, tuple(params)).fetchall()

        datasets_by_esp = defaultdict(lambda: {"data": [], "esp_id": None, "label": None})
        min_rssi_overall = 0
        max_rssi_overall = -120

        if not results_raw:
            app.logger.info(f"No RSSI data found for MAC {mac_address} with current filters.")
            return jsonify({"datasets": [], "min_rssi": -100, "max_rssi": 0})


        for row in results_raw:
            esp_id = row['esp_device_id']
            timestamp_local_str = convert_utc_to_local_string(row['timestamp_utc'], TARGET_TIMEZONE_PYTZ)
            rssi_val = row['ble_rssi']

            datasets_by_esp[esp_id]["esp_id"] = esp_id
            datasets_by_esp[esp_id]["label"] = f"RSSI @ {esp_id}"
            datasets_by_esp[esp_id]["data"].append({"x": timestamp_local_str, "y": rssi_val})
            
            if rssi_val is not None:
                if rssi_val < min_rssi_overall: min_rssi_overall = rssi_val
                if rssi_val > max_rssi_overall: max_rssi_overall = rssi_val
        
        # Si no se encontraron valores RSSI válidos, establecer rangos por defecto
        if min_rssi_overall == 0 and max_rssi_overall == -120:
            min_rssi_overall = -100 # Default min if no data
            max_rssi_overall = 0    # Default max if no data
        else: # Ajustar un poco para que los puntos no queden justo en el borde
            min_rssi_overall = math.floor(min_rssi_overall / 10.0) * 10 - 5
            max_rssi_overall = math.ceil(max_rssi_overall / 10.0) * 10 + 5
            if min_rssi_overall > -30: min_rssi_overall = -30 # Cota inferior razonable
            if max_rssi_overall < -70: max_rssi_overall = -70 # Cota superior razonable


        final_datasets = list(datasets_by_esp.values())
        return jsonify({"datasets": final_datasets, "min_rssi": min_rssi_overall, "max_rssi": max_rssi_overall})

    except sqlite3.Error as e:
        app.logger.error(f"Error de BD en device_rssi_trend para {mac_address}: {e}", exc_info=True)
        return jsonify({"error": "Database error"}), 500
    except Exception as e:
        app.logger.error(f"Error inesperado en device_rssi_trend para {mac_address}: {e}", exc_info=True)
        return jsonify({"error": "Unexpected server error"}), 500
    finally:
        if conn: conn.close()


@app.route('/api/esp-rssi-distribution/<esp_id>')
def esp_rssi_distribution_advanced(esp_id):
    app.logger.info(f"Solicitud GET para /api/esp-rssi-distribution/{esp_id}")
    if not esp_id: # El ESP ID es parte de la URL, Flask debería dar 404 si no está, pero validamos.
        return jsonify({"error": "ESP ID is required"}), 400

    start_date_str = request.args.get('startDate')
    end_date_str = request.args.get('endDate')

    params = [esp_id]
    sql_conditions = ["esp_device_id = ?", "ble_rssi IS NOT NULL"]

    start_date_obj, end_date_obj = None, None
    if start_date_str:
        start_date_obj = validate_date_format(start_date_str)
        if not start_date_obj: return jsonify({"error": "Invalid startDate format. Use YYYY-MM-DD."}), 400
        sql_conditions.append(f"date(datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) >= date(?)")
        params.append(start_date_obj.strftime('%Y-%m-%d'))

    if end_date_str:
        end_date_obj = validate_date_format(end_date_str)
        if not end_date_obj: return jsonify({"error": "Invalid endDate format. Use YYYY-MM-DD."}), 400
        sql_conditions.append(f"date(datetime(timestamp, '{SQLITE_ANALYTICS_TIME_OFFSET}')) <= date(?)")
        params.append(end_date_obj.strftime('%Y-%m-%d'))

    if start_date_obj and end_date_obj and start_date_obj > end_date_obj:
        return jsonify({"error": "startDate cannot be after endDate."}), 400

    query = f"""
        SELECT
            CASE
                WHEN ble_rssi >= -50 THEN '-50 a 0 dBm'
                WHEN ble_rssi >= -60 THEN '-60 a -51 dBm'
                WHEN ble_rssi >= -70 THEN '-70 a -61 dBm'
                WHEN ble_rssi >= -80 THEN '-80 a -71 dBm'
                WHEN ble_rssi >= -90 THEN '-90 a -81 dBm'
                ELSE '< -90 dBm'
            END as rssi_range,
            COUNT(*) as count,
            MIN(ble_rssi) as min_rssi_in_range -- Para ordenar correctamente los rangos
        FROM scanned_devices
        WHERE {' AND '.join(sql_conditions)}
        GROUP BY rssi_range
        ORDER BY min_rssi_in_range DESC;
    """
    conn = None
    try:
        conn = get_db_connection()
        app.logger.debug(f"Executing query for esp-rssi-distribution: {query} with params: {params}")
        results_raw = conn.execute(query, tuple(params)).fetchall()

        if not results_raw:
            app.logger.info(f"No RSSI distribution data found for ESP {esp_id} with current filters.")
            # Devolver estructura vacía esperada por Chart.js pie/bar
            return jsonify({"labels": [], "data": []})

        # Asegurar el orden correcto de las etiquetas si algunos rangos no tienen datos
        all_possible_ranges = ['-50 a 0 dBm', '-60 a -51 dBm', '-70 a -61 dBm', '-80 a -71 dBm', '-90 a -81 dBm', '< -90 dBm']
        counts_by_range = {row['rssi_range']: row['count'] for row in results_raw}
        
        labels = all_possible_ranges
        data_counts = [counts_by_range.get(r, 0) for r in all_possible_ranges]
        
        return jsonify({"labels": labels, "data": data_counts})

    except sqlite3.Error as e:
        app.logger.error(f"Error de BD en esp_rssi_distribution para {esp_id}: {e}", exc_info=True)
        return jsonify({"error": "Database error"}), 500
    except Exception as e:
        app.logger.error(f"Error inesperado en esp_rssi_distribution para {esp_id}: {e}", exc_info=True)
        return jsonify({"error": "Unexpected server error"}), 500
    finally:
        if conn: conn.close()


# --- Ejecución del Servidor ---
if __name__ == '__main__':
    app.logger.info("Iniciando servidor backend BLE...")
    app.logger.info(f"Zona horaria para visualización: {TARGET_TIMEZONE_PYTZ.zone}")
    app.logger.info(f"Offset para analíticas SQL (¡REVISAR DST!): {SQLITE_ANALYTICS_TIME_OFFSET}")
    init_db() 
    app.run(host=SERVER_HOST, port=SERVER_PORT, debug=True)
