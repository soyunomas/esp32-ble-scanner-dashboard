import json
import yaml # Necesitarás instalar PyYAML: pip install PyYAML
import os

# Fuente: https://www.bluetooth.com/specifications/assigned-numbers/company-identifiers/
# Esta es una lista parcial. Se puede expandir según necesidad o cargar desde un archivo.
COMPANY_IDENTIFIERS = {
    0x0000: "Ericsson Technology Licensing",
    0x0001: "Nokia Mobile Phones",
    0x0002: "Intel Corp.",
    0x0003: "IBM Corp.",
    0x0004: "Toshiba Corp.",
    0x0005: "3Com",
    0x0006: "Microsoft",
    0x0007: "Lucent",
    0x0008: "Motorola",
    0x0009: "Infineon Technologies AG",
    0x000A: "Qualcomm Technologies International, Ltd. (QTIL)", # CSR
    0x000B: "Silicon Wave",
    0x000C: "Digianswer A/S",
    0x000D: "Texas Instruments Inc.",
    0x000E: "Parthus Technologies Inc.",
    0x000F: "Broadcom Corporation",
    0x0010: "Mitel Semiconductor",
    0x0011: "Widcomm, Inc.",
    0x0012: "Zeevo, Inc.",
    0x0013: "Atmel Corporation",
    0x0014: "Mitsubishi Electric Corporation",
    0x0015: "RTX Telecom A/S",
    0x0016: "KC Technology Inc.",
    0x0017: "Newlogic",
    0x0018: "Transilica, Inc.",
    0x0019: "Rohde & Schwarz GmbH & Co. KG",
    0x001A: "TTPCom Limited",
    0x001B: "Signia Technologies, Inc.",
    0x001C: "Conexant Systems Inc.",
    0x001D: "Qualcomm",
    0x001E: "Inventel",
    0x001F: "AVM Berlin",
    0x0020: "BandSpeed, Inc.",
    0x0021: "Mansella Ltd",
    0x0022: "NEC Corporation",
    0x0023: "WavePlus Technology Co., Ltd.",
    0x0024: "Alcatel",
    0x0025: "NXP Semiconductors (formerly Philips Semiconductors)",
    0x0026: "C Technologies",
    0x0027: "OpenInterface",
    0x0028: "R F Micro Devices",
    0x0029: "Hitachi Ltd",
    0x002A: "Symbol Technologies, Inc.",
    0x002B: "Tenovis",
    0x002C: "Macronix International Co. Ltd.",
    0x002D: "GCT Semiconductor",
    0x002E: "Norwood Systems",
    0x002F: "MewTel Technology Inc.",
    0x0030: "ST Microelectronics",
    0x0031: "Synopsys, Inc.", # Corregido nombre
    0x0032: "Red-M (Communications) Ltd",
    0x0033: "Commil Ltd",
    0x0034: "Computer Access Technology Corporation (CATC)",
    0x0035: "Eclipse (HQ Espana) S.L.",
    0x0036: "Renesas Electronics Corporation",
    0x0037: "Mobilian Corporation",
    0x0038: "Terax",
    0x0039: "Integrated System Solution Corp.",
    0x003A: "Matsushita Electric Industrial Co., Ltd.",
    0x003B: "Gennum Corporation",
    0x003C: "BlackBerry Limited (formerly Research In Motion)",
    0x003D: "IPCom GmbH",
    0x003E: "TDK Corporation",
    0x003F: "LG Electronics",
    0x0040: "Seiko Epson Corporation",
    0x0041: "Integrated Silicon Solution Taiwan, Inc.",
    0x0042: "CONWISE Technology Corporation Ltd",
    0x0043: "PARROT AUTOMOTIVE SAS",
    0x0044: "Socket Mobile",
    0x0045: "Atheros Communications, Inc.",
    0x0046: "MediaTek, Inc.",
    0x0047: "Bluegiga", # Now Silicon Labs
    0x0048: "Marvell Technology Group Ltd.",
    0x0049: "3DSP Corporation",
    0x004A: "Accel Semiconductor Ltd.",
    0x004B: "Continental Automotive Systems",
    0x004C: "Apple, Inc.",
    0x004D: "Staccato Communications, Inc.",
    0x004E: "Avago Technologies",
    0x004F: "APT Ltd.",
    0x0050: "SiRF Technology, Inc.",
    0x0051: "Tzero Technologies, Inc.",
    0x0052: "J&M Corporation",
    0x0053: "Free2move AB",
    0x0056: "Sony Mobile Communications (formerly Sony Ericsson Mobile Communications AB)", # Actualizado
    0x0057: "GN Netcom A/S", # Jabra
    0x0058: "GN ReSound A/S",
    0x0059: "Jawbone (Aliph, Inc.)", # Actualizado
    0x005A: "Topcon Positioning Systems, LLC",
    0x005C: "Qualcomm Datacenter Technologies, Inc.",
    0x005D: "Nordic Semiconductor ASA",
    0x006E: "Realtek Semiconductor Corporation",
    0x0075: "Samsung Electronics Co. Ltd.",
    0x00D7: "Xiaomi Communications Co., Ltd.",
    0x0104: "Ruuvicon Oy (formerly Ruuvi Innovations Ltd.)", # Para RuuviTag, actualizado
    0x0157: "Espressif Inc.",
    0x02E0: "Google LLC", # Actualizado
    0x0499: "Telink Semiconductor (Taipei) Co. Ltd.",
    0xFFFF: "Unassigned (Company ID 0xFFFF is reserved for internal use or prototyping)"
    # ... Añadir más según sea necesario.
}

# --- INICIO DE CÓDIGO PARA CARGAR DESDE YAML ---
def _load_and_merge_company_identifiers_from_yaml(
    base_identifiers, yaml_filepath="company_identifiers.yaml"
):
    """
    Carga identificadores de compañía desde un archivo YAML y los fusiona
    con un diccionario base. Las entradas del YAML sobrescribirán las existentes
    si las claves (IDs numéricos) coinciden.
    """
    if not os.path.exists(yaml_filepath):
        print(f"Advertencia: El archivo YAML '{yaml_filepath}' no fue encontrado. No se cargarán identificadores adicionales.")
        return base_identifiers

    try:
        with open(yaml_filepath, 'r', encoding='utf-8') as f:
            data_from_yaml = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"Error al parsear el archivo YAML '{yaml_filepath}': {e}")
        return base_identifiers
    except Exception as e:
        print(f"Error inesperado al leer el archivo YAML '{yaml_filepath}': {e}")
        return base_identifiers

    if not isinstance(data_from_yaml, dict) or 'company_identifiers' not in data_from_yaml:
        print(f"Advertencia: El archivo YAML '{yaml_filepath}' no tiene la clave raíz 'company_identifiers' esperada o no es un diccionario.")
        return base_identifiers

    yaml_list = data_from_yaml.get('company_identifiers')

    if not isinstance(yaml_list, list):
        print(f"Advertencia: El valor de 'company_identifiers' en '{yaml_filepath}' no es una lista.")
        return base_identifiers

    loaded_count = 0
    updated_count = 0
    for item in yaml_list:
        if isinstance(item, dict) and 'value' in item and 'name' in item:
            try:
                company_id_from_yaml = item['value']
                company_id = None

                if isinstance(company_id_from_yaml, str):
                    # Si es un string, intentar convertir.
                    # int(string, 0) auto-detecta la base por prefijo (0x, 0o, 0b)
                    # o asume base 10 si no hay prefijo.
                    company_id = int(company_id_from_yaml, 0)
                elif isinstance(company_id_from_yaml, int):
                    # Si ya es un entero, se usa directamente.
                    # Esto cubre casos como `value: 7` o `value: 0x07` (sin comillas) en YAML,
                    # que PyYAML convierte a un tipo int de Python.
                    company_id = company_id_from_yaml
                else:
                    # Si no es ni string ni int, es un tipo no esperado.
                    print(f"Advertencia: Tipo de 'value' no esperado ({type(company_id_from_yaml)}) para el item {item} en '{yaml_filepath}'. Se omitirá.")
                    continue # Saltar este item y procesar el siguiente

                company_name = item['name']
                
                if company_id in base_identifiers:
                    updated_count +=1
                else:
                    loaded_count +=1
                base_identifiers[company_id] = company_name
            
            except ValueError as e: # Error en la conversión de string a int (e.g., int("texto_no_numerico", 0))
                print(f"Advertencia: Valor de ID inválido en 'value': '{item.get('value')}' (error de conversión: {e}) en el item {item} de '{yaml_filepath}'. Se omitirá.")
            except Exception as e: # Otros errores inesperados durante el procesamiento del item
                print(f"Advertencia: Error general procesando la entrada '{item}' de '{yaml_filepath}': {e}. Se omitirá.")
        else:
            print(f"Advertencia: Formato de item inválido en '{yaml_filepath}': {item}. Debe ser un diccionario con claves 'value' y 'name'. Se omitirá.")
            
    if loaded_count > 0 or updated_count > 0:
        print(f"Información: Se cargaron {loaded_count} nuevos identificadores y se actualizaron {updated_count} desde '{yaml_filepath}'.")
    
    return base_identifiers

# Cargar los identificadores del YAML y fusionarlos con los definidos en el script.
script_dir = os.path.dirname(os.path.abspath(__file__)) # Directorio del script actual
yaml_file_path = os.path.join(script_dir, "company_identifiers.yaml")
COMPANY_IDENTIFIERS = _load_and_merge_company_identifiers_from_yaml(COMPANY_IDENTIFIERS, yaml_file_path)

# --- FIN DE CÓDIGO PARA CARGAR DESDE YAML ---

# Fuente: https://www.bluetooth.com/specifications/gatt/services/
# Esta es una lista parcial.
SERVICE_UUIDS_NAMES = {
    "00001800-0000-1000-8000-00805f9b34fb": "Generic Access",
    "00001801-0000-1000-8000-00805f9b34fb": "Generic Attribute",
    # ... (resto de tus UUIDs de servicio) ...
    "0000fe95-0000-1000-8000-00805f9b34fb": "Xiaomi Mi Service",
    "0000fef3-0000-1000-8000-00805f9b34fb": "Google Fast Pair Service",
    "0000fdab-0000-1000-8000-00805f9b34fb": "Google (Eddystone/Nearby)" 
}

def parse_manufacturer_data(hex_string):
    """
    Parsea el manufacturer_data hexadecimal.
    Devuelve el nombre del fabricante, los datos específicos del fabricante y el Company ID en formato hexadecimal.
    Ejemplo: "4C000215..." (Apple iBeacon) -> ("Apple, Inc.", "0215...", "0x004C")
    """
    if not hex_string or not isinstance(hex_string, str) or len(hex_string) < 4:
        return "N/A", hex_string if isinstance(hex_string, str) else "", "N/A"

    try:
        cid_hex_le = hex_string[0:4]
        cid = int(cid_hex_le[2:4] + cid_hex_le[0:2], 16) 
        
        company_name = COMPANY_IDENTIFIERS.get(cid, f"Unknown CID") # Usa el diccionario actualizado
        company_id_hex_str = f"0x{cid:04X}"
        
        remaining_data = hex_string[4:]
        return company_name, remaining_data, company_id_hex_str
    except ValueError:
        return "N/A (Parse Error)", hex_string, "N/A"
    except Exception:
        return "N/A (General Error)", hex_string, "N/A"

def get_service_uuid_name(uuid_str):
    """Retorna el nombre conocido de un Service UUID, o el mismo UUID si no se conoce."""
    return SERVICE_UUIDS_NAMES.get(uuid_str.lower(), uuid_str)


# --- Ejemplo de uso (opcional, para probar) ---
if __name__ == "__main__":
    print("Probando COMPANY_IDENTIFIERS después de cargar YAML:")
    # Verificar si se cargaron los del YAML
    print(f"MAERSK (0x0EE0): {COMPANY_IDENTIFIERS.get(0x0EE0)}")
    print(f"Dynaudio (0x0EDF): {COMPANY_IDENTIFIERS.get(0x0EDF)}")
    # Verificar uno de los originales
    print(f"Apple (0x004C): {COMPANY_IDENTIFIERS.get(0x004C)}")
    print(f"Total de identificadores cargados: {len(COMPANY_IDENTIFIERS)}")

    print("\nProbando parse_manufacturer_data:")
    # Ejemplo con un CID del YAML (MAERSK CONTAINER INDUSTRY A/S, 0x0EE0, se transmite como E00E)
    name, data, cid_hex = parse_manufacturer_data("E00Eaabbcc")
    print(f"Hex: E00Eaabbcc -> Name: {name}, Data: {data}, CID: {cid_hex}")

    # Ejemplo con un CID original (Apple, 0x004C, se transmite como 4C00)
    name, data, cid_hex = parse_manufacturer_data("4C0002150102030405060708090A0B0C0D0E0F10111213C5")
    print(f"Hex: 4C00... -> Name: {name}, Data: {data}, CID: {cid_hex}")
    
    # Ejemplo con un CID desconocido
    name, data, cid_hex = parse_manufacturer_data("ABCD010203") # ABCD -> CDAB (52651)
    print(f"Hex: ABCD010203 -> Name: {name}, Data: {data}, CID: {cid_hex}")
