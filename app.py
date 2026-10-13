import os
import io
import json
import uuid
import re
import sqlite3
import qrcode
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit # NUEVO

# --- NUEVAS LIBRERÍAS PARA WHATSAPP ---
import threading
import urllib.parse
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# --- Inicio colaboracion de Fredy Talero

app = Flask(__name__)
app.secret_key = 'clave_secreta_cine_app'
socketio = SocketIO(app, cors_allowed_origins="*") # NUEVO: Inicializamos el "túnel"

def obtener_etiqueta_fila(indice_fila):
    """
    Convierte un índice numérico de fila (0, 1, 2... 25, 26) a su equivalente en abecedario:
    0 -> A, 1 -> B ... 25 -> Z, 26 -> AA, 27 -> AB
    """
    etiqueta = ""
    while indice_fila >= 0:
        etiqueta = chr(65 + (indice_fila % 26)) + etiqueta
        indice_fila = (indice_fila // 26) - 1
    return etiqueta

def limpiar_sillas_texto(texto):
    """
    Elimina caracteres no deseados (corchetes, comillas, barras, saltos de linea)
    y retorna las sillas formateadas en comas (ej: "A1, A2, B5").
    """
    if not texto:
        return ""
    texto_limpio = re.sub(r"[\[\]'\"\\/\r\n]", "", str(texto))
    lista = [s.strip().upper() for s in texto_limpio.split(',') if s.strip()]
    return ", ".join(lista)

app.secret_key = 'clave_secreta_cine_app'

# Directorios de archivos estáticos
UPLOAD_FOLDER = os.path.join('static', 'uploads', 'combos')
TICKETS_FOLDER = os.path.join('static', 'tickets')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['TICKETS_FOLDER'] = TICKETS_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(TICKETS_FOLDER, exist_ok=True)

DB_NAME = 'database.db'

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS zonas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            filas_inicio INTEGER NOT NULL,
            filas_fin INTEGER NOT NULL,
            precio REAL NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS combos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            precio REAL NOT NULL,
            imagen TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cedula TEXT NOT NULL,
            nombre TEXT NOT NULL,
            celular TEXT NOT NULL,
            asientos TEXT NOT NULL,
            combos_json TEXT,
            total REAL NOT NULL,
            metodo_pago TEXT NOT NULL,
            codigo_boleta TEXT UNIQUE NOT NULL,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS configuracion (
            clave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        )
    ''')

    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("filas", "15")')
    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("columnas", "10")')
    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("sillas_bloque", "5")')
    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("filas_vip", "3")')
    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("sillas_inhabilitadas", "")')

    # Campos Editables de la Boleta
    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("boleta_encabezado_izq", "MINISTERIO DE MEDIOS")')
    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("boleta_encabezado_der", "VERDAD EN ACCION")')
    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("boleta_subtitulo", "CINE EN LA IGLESIA")')
    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("boleta_titulo", "THE CHOSEN")')
    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("boleta_detalles_titulo", "Temporada 6 - 3 capitulos")')
    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("boleta_fecha", "Por confirmar")')
    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("boleta_hora", "Por confirmar")')
    cursor.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("boleta_lugar", "Por confirmar")')

    cursor.execute('SELECT COUNT(*) FROM zonas')
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO zonas (nombre, filas_inicio, filas_fin, precio) VALUES ('Tipo A', 1, 5, 13000)")
        cursor.execute("INSERT INTO zonas (nombre, filas_inicio, filas_fin, precio) VALUES ('Tipo B', 6, 10, 11000)")
        cursor.execute("INSERT INTO zonas (nombre, filas_inicio, filas_fin, precio) VALUES ('Tipo C', 11, 15, 10000)")

    conn.commit()
    conn.close()

init_db()

# ----------------------------------------------------
#  RUTAS DE LA APLICACIÓN / FLUJO DE VENTA
# ----------------------------------------------------

@app.route('/pantalla_cliente')
def pantalla_cliente():
    # Esta es la ruta que se abrirá en el televisor (F11 - Pantalla Completa)
    return render_template('pantalla_cliente.html')

@app.route('/')
def index():
    session.clear()
    return render_template('index.html')

@app.route('/sala', methods=['GET', 'POST'])
def sala():
    if request.method == 'POST':
        session['cliente'] = {
            'cedula': request.form.get('cedula'),
            'nombre': request.form.get('nombre'),
            'celular': request.form.get('celular'),
            'cantidad': int(request.form.get('cantidad', 1))
        }

    if 'cliente' not in session:
        return redirect(url_for('index'))

    conn = get_db_connection()
    # 1. Obtener asientos ocupados de las ventas
    ventas = conn.execute('SELECT asientos FROM ventas').fetchall()
    asientos_ocupados = []
    for v in ventas:
        try:
            asientos_list = json.loads(v['asientos'])
            for a in asientos_list:
                asientos_ocupados.append(str(a['codigo']).strip().upper())
        except Exception:
            pass

    # 2. Cargar configuración de la sala
    config_raw = conn.execute('SELECT clave, valor FROM configuracion').fetchall()
    conn.close()
    
    config = {row['clave']: row['valor'] for row in config_raw}
    num_filas = int(config.get('filas', 15))
    num_columnas = int(config.get('columnas', 10))
    sillas_bloque = int(config.get('sillas_bloque', 5))
    filas_vip = int(config.get('filas_vip', 3))
    
    raw_inhabilitadas = config.get('sillas_inhabilitadas', '')
    texto_limpio_inh = limpiar_sillas_texto(raw_inhabilitadas)
    sillas_inhabilitadas = [s.strip().upper() for s in texto_limpio_inh.split(',') if s.strip()]

    sillas_por_nave = sillas_bloque if sillas_bloque > 0 else 5
    cant_naves = 2
    naves = []

    for n_idx in range(cant_naves):
        col_inicio = n_idx * sillas_por_nave
        col_fin = col_inicio + sillas_por_nave
        
        filas_nave = []
        for f_idx in range(num_filas):
            etiqueta_fila = obtener_etiqueta_fila(f_idx)
            
            # Si es la Nave 2 (índice 1), duplicamos la letra (A -> AA, B -> BB)
            if n_idx == 1:
                etiqueta_fila = etiqueta_fila * 2
                
            es_vip = f_idx < filas_vip
            asientos_fila = []

            for c_idx in range(col_inicio, col_fin):
                num_silla_local = c_idx - col_inicio + 1
                # El código de la silla ahora usa el número local (1, 2, 3) en lugar de continuar (6, 7, 8)
                codigo_silla = f"{etiqueta_fila}{num_silla_local}"
                
                estado = 'disponible'
                if codigo_silla in sillas_inhabilitadas:
                    estado = 'inhabilitado'
                elif codigo_silla in asientos_ocupados:
                    estado = 'ocupado'

                asientos_fila.append({
                    'codigo': codigo_silla,
                    'num_silla': num_silla_local,
                    'estado': estado,
                    'es_vip': es_vip
                })

            filas_nave.append({
                'etiqueta': etiqueta_fila,
                'asientos': asientos_fila
            })

        naves.append({
            'numero': n_idx + 1,
            'filas': filas_nave
        })

    return render_template('sala.html', 
                           cliente=session['cliente'], 
                           naves=naves, 
                           config=config)

@app.route('/combos', methods=['GET', 'POST'])
def combos():
    if request.method == 'POST':
        asientos_raw = request.form.get('asientos_json')
        if asientos_raw:
            session['asientos'] = json.loads(asientos_raw)

    if 'asientos' not in session or 'cliente' not in session:
        return redirect(url_for('index'))

    conn = get_db_connection()
    combos_db = conn.execute('SELECT * FROM combos').fetchall()
    conn.close()

    total_boletas_precio = sum(a['precio'] for a in session['asientos'])
    total_boletas_cant = len(session['asientos'])

    return render_template('combos.html', 
                           combos=combos_db, 
                           total_boletas_precio=total_boletas_precio,
                           total_boletas_cant=total_boletas_cant)

@app.route('/pago', methods=['GET', 'POST'])
def pago():
    if request.method == 'POST':
        combos_raw = request.form.get('combos_json')
        if combos_raw:
            session['combos'] = json.loads(combos_raw)

    if 'cliente' not in session or 'asientos' not in session:
        return redirect(url_for('index'))

    combos_seleccionados = session.get('combos', [])
    
    conn = get_db_connection()
    combos_detalle = []
    total_combos = 0

    for c in combos_seleccionados:
        c_db = conn.execute('SELECT nombre FROM combos WHERE id = ?', (c['id'],)).fetchone()
        nombre = c_db['nombre'] if c_db else f"Combo #{c['id']}"
        subtotal = c['cantidad'] * c['precio']
        total_combos += subtotal
        combos_detalle.append({
            'nombre': nombre,
            'cantidad': c['cantidad'],
            'precio': c['precio'],
            'subtotal': subtotal
        })
    conn.close()

    session['combos_detalle'] = combos_detalle

    total_boletas = sum(a['precio'] for a in session['asientos'])
    total_final = total_boletas + total_combos
    session['total_final'] = total_final

    return render_template('pago.html', 
                           cliente=session['cliente'], 
                           asientos=session['asientos'], 
                           combos_detalle=combos_detalle, 
                           total_final=total_final)

@app.route('/confirmar_pago', methods=['POST'])
def confirmar_pago():
    if 'cliente' not in session or 'asientos' not in session:
        return redirect(url_for('index'))

    cliente = session['cliente']
    asientos = session['asientos']
    combos_detalle = session.get('combos_detalle', [])
    total_final = session.get('total_final', 0)
    metodo_pago = request.form.get('metodo_pago', 'Efectivo')

    conn = get_db_connection()
    
    # --- Lógica de código secuencial inteligente ---
    ventas_previas = conn.execute('SELECT asientos FROM ventas').fetchall()
    total_asientos_vendidos = 0
    for v in ventas_previas:
        try:
            asientos_list = json.loads(v['asientos'])
            total_asientos_vendidos += len(asientos_list)
        except Exception:
            pass
    
    # Sumamos 1 al total de asientos vendidos para hallar el consecutivo actual
    numero_secuencia = total_asientos_vendidos + 1
    
    # Obtenemos el código del primer asiento (ej. 'B1')
    primer_asiento = asientos[0]['codigo'] if asientos else "XX"
    
    # Generamos el código uniendo el primer asiento con el consecutivo de 4 dígitos
    codigo_boleta = f"{primer_asiento}{numero_secuencia:04d}"

    conn.execute('''
        INSERT INTO ventas (cedula, nombre, celular, asientos, combos_json, total, metodo_pago, codigo_boleta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        cliente['cedula'],
        cliente['nombre'],
        cliente['celular'],
        json.dumps(asientos),
        json.dumps(combos_detalle),
        total_final,
        metodo_pago,
        codigo_boleta
    ))
    conn.commit()
    conn.close()

    # 1. Generar boleta principal de Entrada
    total_asientos = sum(a['precio'] for a in asientos)
    ruta_entrada = generar_imagen_entrada(cliente, asientos, total_asientos, codigo_boleta)
    rutas_generadas = [ruta_entrada]

    # 2. Generar boleta de Comida (si compraron combos)
    if combos_detalle:
        total_combos = sum(c['subtotal'] for c in combos_detalle)
        ruta_combo = generar_imagen_combo(cliente, combos_detalle, total_combos, codigo_boleta)
        rutas_generadas.append(ruta_combo)

    # 3. Envío de WhatsApp (Le pasamos la lista de TODAS las imágenes generadas)
    # enviar_whatsapp(cliente['celular'], rutas_generadas) # DESACTIVADO: Selenium no corre en Render

    return render_template('boleta.html', 
                           cliente=cliente, 
                           codigo_boleta=codigo_boleta, 
                           rutas_boletas=rutas_generadas)

### ----------------------------------------------------
### WEBSOCKETS - REBOTADOR DE EVENTOS
### ----------------------------------------------------
@socketio.on('evento_cajero')
def rebotar_evento(datos):
    # Recibe la información del cajero y la empuja a la pantalla del cliente
    emit('evento_tv', datos, broadcast=True)

### ----------------------------------------------------
### GENERADOR DE BOLETAS EN IMAGEN (PLANTILLA FIJA)
### ----------------------------------------------------

def generar_imagen_entrada(cliente, asientos, total_boletas, codigo_boleta):
    ruta_base = os.path.join('static', 'boleta-final.gif')
    imagen = Image.open(ruta_base).convert('RGB')
    draw = ImageDraw.Draw(imagen)
    
    color_blanco = '#ffffff'
    color_oro = '#d4af37'
    color_gris = '#cbd5e1'
    
    x_right = 780 
    
    # --- CAMBIO APLICADO: Configuración de Tipografías con Negritas ---
    try:
        font_title = ImageFont.truetype("arial.ttf", 26)
        font_subtitle = ImageFont.truetype("arial.ttf", 10)
        font_labels = ImageFont.truetype("arial.ttf", 10)
        font_values = ImageFont.truetype("arialbd.ttf", 12) # bd = bold (negrita)
        font_large_val = ImageFont.truetype("arialbd.ttf", 16)
    except IOError:
        # Respaldo por si el PC no tiene la fuente Arial instalada
        font_title = font_subtitle = font_labels = font_values = font_large_val = ImageFont.load_default()
    
    # 2. Textos de la cabecera
    draw.text((x_right, 45), "ENTRADA", fill=color_oro, font=font_title)
    draw.text((x_right, 80), f"VÁLIDA PARA {len(asientos)} PERSONA(S)", fill=color_blanco, font=font_subtitle)
    
    # 3. Código QR
    qr = qrcode.QRCode(box_size=4, border=1)
    
    # --- CAMBIO APLICADO: Formato de QR avanzado para Entrada (Detecta Nave 1 y Nave 2) ---
    import re
    asientos_formateados = []
    for a in asientos:
        # Separa las letras del número (ej: 'AA2' -> 'AA' y '2')
        match = re.match(r"([A-Za-z]+)(\d+)", a['codigo'])
        if match:
            letras = match.group(1)
            # Si tiene más de una letra es NAVE 2 (AA, BB, etc.), sino NAVE 1 (A, B, etc.)
            nave_str = "NAVE 2" if len(letras) > 1 else "NAVE 1"
            asientos_formateados.append(f"({nave_str}){a['codigo']}")
        else:
            asientos_formateados.append(a['codigo'])
            
    texto_asientos_qr = " - ".join(asientos_formateados)
    
    qr_data = f'TIPO: CINE | TICKET:{codigo_boleta} | ASIENTOS : "{texto_asientos_qr}"'
    
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB').resize((110, 110))
    imagen.paste(qr_img, (x_right + 35, 105))
    
    # 4. Información dinámica
    y_stub = 230
    codigos_asientos = [a['codigo'] for a in asientos]
    
    # ASIENTOS
    draw.text((x_right, y_stub), "ASIENTOS:", fill=color_oro, font=font_labels)
    y_stub += 15
    draw.rectangle([x_right, y_stub + 3, x_right + 6, y_stub + 9], fill=color_oro) # Cuadrito dorado
    draw.text((x_right + 12, y_stub), ", ".join(codigos_asientos), fill=color_blanco, font=font_values)
    
    # NÚMERO DE BOLETA
    y_stub += 25
    num_boleta = codigo_boleta.split('-')[-1] if '-' in codigo_boleta else codigo_boleta
    draw.rectangle([x_right, y_stub + 4, x_right + 6, y_stub + 10], fill=color_oro) # Cuadrito dorado
    draw.text((x_right + 12, y_stub), f"No. {num_boleta}", fill=color_oro, font=font_large_val)
    
    # VALOR
    y_stub += 25
    draw.text((x_right, y_stub), f"VALOR: ${total_boletas:,.0f} COP", fill=color_blanco, font=font_values)
    
    # TITULAR Y CÉDULA
    y_stub += 25
    draw.text((x_right, y_stub), "TITULAR", fill=color_gris, font=font_labels)
    
    import textwrap
    lineas_nombre = textwrap.wrap(cliente['nombre'].upper(), width=15)
    for i, linea in enumerate(lineas_nombre[:2]):
        y_stub += 14
        if i == 0:
            draw.rectangle([x_right, y_stub + 3, x_right + 6, y_stub + 9], fill=color_oro) # Cuadrito dorado en primera línea
            draw.text((x_right + 12, y_stub), linea, fill=color_gris, font=font_values)
        else:
            draw.text((x_right + 12, y_stub), linea, fill=color_gris, font=font_values)
            
    y_stub += 18
    draw.text((x_right, y_stub), f"C.C: {cliente['cedula']}", fill=color_gris, font=font_values)
    
    filename = f"{codigo_boleta}-ENTRADA.png"
    filepath = os.path.join(app.config['TICKETS_FOLDER'], filename)
    imagen.save(filepath)
    return f"tickets/{filename}"


def generar_imagen_combo(cliente, combos, total_combos, codigo_boleta):
    ruta_base = os.path.join('static', 'boleta-final.gif')
    imagen = Image.open(ruta_base).convert('RGB')
    draw = ImageDraw.Draw(imagen)
    
    color_blanco = '#ffffff'
    color_oro = '#d4af37'
    color_gris = '#cbd5e1'
    x_right = 780 
    
    # --- CAMBIO APLICADO: Configuración de Tipografías con Negritas ---
    try:
        font_title = ImageFont.truetype("arial.ttf", 24)
        font_labels = ImageFont.truetype("arial.ttf", 10)
        font_values = ImageFont.truetype("arialbd.ttf", 12)
        font_large_val = ImageFont.truetype("arialbd.ttf", 14)
    except IOError:
        font_title = font_labels = font_values = font_large_val = ImageFont.load_default()
    
    draw.text((x_right, 45), "TICKET COMIDA", fill=color_oro, font=font_title)
    
    qr = qrcode.QRCode(box_size=4, border=1)
    
    # --- CAMBIO APLICADO: Formato de QR para Combos de Comida ---
    # Esto creará un texto tipo: "Perro Caliente" X"2" - "Gaseosa" X"1"
    combos_formateados = [f'"{c["nombre"]}" X"{c["cantidad"]}"' for c in combos]
    texto_combos_qr = " - ".join(combos_formateados)
    
    qr_data = f"TIPO:COMIDA | TICKET:{codigo_boleta} | {texto_combos_qr}"
    
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB').resize((110, 110))
    imagen.paste(qr_img, (x_right + 35, 80))
    
    y_stub = 200
    draw.text((x_right, y_stub), "INCLUYE:", fill=color_oro, font=font_labels)
    y_stub += 15
    
    import textwrap
    for c in combos:
        texto_combo = f"{c['cantidad']}x {c['nombre']}"
        lineas = textwrap.wrap(texto_combo, width=16)
        for i, linea in enumerate(lineas):
            if i == 0:
                draw.rectangle([x_right, y_stub + 3, x_right + 6, y_stub + 9], fill=color_oro) # Cuadrito por cada combo
                draw.text((x_right + 12, y_stub), linea, fill=color_blanco, font=font_values)
            else:
                draw.text((x_right + 12, y_stub), linea, fill=color_blanco, font=font_values)
            y_stub += 15
    
    y_stub += 10
    draw.text((x_right, y_stub), f"VALOR: ${total_combos:,.0f} COP", fill=color_blanco, font=font_large_val)
    
    y_stub += 25
    draw.text((x_right, y_stub), "TITULAR:", fill=color_gris, font=font_labels)
    
    lineas_nombre = textwrap.wrap(cliente['nombre'].upper(), width=15)
    for i, linea in enumerate(lineas_nombre[:2]):
        y_stub += 14
        if i == 0:
            draw.rectangle([x_right, y_stub + 3, x_right + 6, y_stub + 9], fill=color_oro)
            draw.text((x_right + 12, y_stub), linea, fill=color_gris, font=font_values)
        else:
            draw.text((x_right + 12, y_stub), linea, fill=color_gris, font=font_values)
    
    filename = f"{codigo_boleta}-COMBOS.png"
    filepath = os.path.join(app.config['TICKETS_FOLDER'], filename)
    imagen.save(filepath)
    return f"tickets/{filename}"

@app.route('/admin/diseno_boleta', methods=['GET', 'POST'])
def diseno_boleta():
    if request.method == 'POST':
        campos = [
            'boleta_encabezado_izq', 'boleta_encabezado_der',
            'boleta_subtitulo', 'boleta_titulo', 'boleta_detalles_titulo',
            'boleta_fecha', 'boleta_hora', 'boleta_lugar'
        ]
        conn = get_db_connection()
        for campo in campos:
            val = request.form.get(campo, '')
            conn.execute('UPDATE configuracion SET valor = ? WHERE clave = ?', (val, campo))
        conn.commit()
        conn.close()

        flash('Diseño de boleta actualizado correctamente.', 'success')
        return redirect(url_for('diseno_boleta'))

    config = obtener_config_boleta()
    return render_template('admin/diseno_boleta.html', config=config)

def tarea_selenium(celular, rutas_imagenes):
    num_limpio = re.sub(r'\D', '', celular)
    if len(num_limpio) == 10:
        num_limpio = f"57{num_limpio}"
        
    mensaje = (
        "Muchas gracias por tu compra, esperamos verte en nuestra tarde de cine con tu familia.\n\n"
        "No olvides: 29 de noviembre a las 2 de la tarde en nuestro auditorio de verdad en acción"
    )
    mensaje_codificado = urllib.parse.quote(mensaje)
    
    ruta_perfil = os.path.abspath("whatsapp_session")
    chrome_options = Options()
    chrome_options.add_argument(f"user-data-dir={ruta_perfil}")
    # Ocultar alertas molestas en la consola
    chrome_options.add_argument("--log-level=3") 
    
    url = f"https://web.whatsapp.com/send?phone={num_limpio}&text={mensaje_codificado}"
    
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        driver.get(url)
        wait = WebDriverWait(driver, 60)
        
        # 1. Enviar el texto primero (Usamos JS para ignorar tooltips o letreros que bloqueen el clic)
        btn_enviar_texto = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'span[data-icon="send"]')))
        driver.execute_script("arguments[0].click();", btn_enviar_texto)
        time.sleep(1.5) # Pausa para que el texto se envíe
        
        # 2. Clic en el Clip de Adjuntar (Soporta Español "Adjuntar" e Inglés "Attach")
        btn_adjuntar = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[title="Adjuntar"], div[title="Attach"]')))
        driver.execute_script("arguments[0].click();", btn_adjuntar)
        time.sleep(1) # Pausa para que suba el menú
        
        # 3. Inyectar TODAS las boletas al input oculto (Selector genérico infalible)
        input_file = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="file"]')))
        rutas_absolutas = [os.path.abspath(os.path.join(app.config['TICKETS_FOLDER'], os.path.basename(r))) for r in rutas_imagenes]
        rutas_str = "\n".join(rutas_absolutas)
        input_file.send_keys(rutas_str)
        
        # 4. Enviar las imágenes generadas
        time.sleep(2) # Esperar a que WhatsApp cargue la vista previa de las fotos
        btn_enviar_img = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'span[data-icon="send"]')))
        driver.execute_script("arguments[0].click();", btn_enviar_img)
        
        # Darle 4 segundos a la barrita de WhatsApp para que termine de enviar antes de cerrar Chrome
        time.sleep(4)
        driver.quit()
        print(f"[WHATSAPP OK] Mensaje y {len(rutas_imagenes)} boleta(s) enviadas con éxito al {num_limpio}")
        
    except Exception as e:
        print(f"[WHATSAPP ERROR] Ocurrió un error en el proceso: {e}")
        try:
            driver.quit()
        except:
            pass

def enviar_whatsapp(celular, rutas_imagenes):
    # Ejecutamos la tarea en un Hilo Paralelo (Threading). 
    # Esto evita que la pantalla del cajero se congele mientras Selenium hace su trabajo.
    hilo = threading.Thread(target=tarea_selenium, args=(celular, rutas_imagenes))
    hilo.start()

# ----------------------------------------------------
#  MÓDULO ADMINISTRACIÓN (SALA Y DASHBOARD)
# ----------------------------------------------------

def obtener_config_sala():
    conn = get_db_connection()
    config_raw = conn.execute('SELECT clave, valor FROM configuracion').fetchall()
    conn.close()
    
    config = {row['clave']: row['valor'] for row in config_raw}
    
    raw_inhabilitadas = config.get('sillas_inhabilitadas', '')
    texto_limpio_inh = limpiar_sillas_texto(raw_inhabilitadas)
    sillas_inh_lista = [s.strip().upper() for s in texto_limpio_inh.split(',') if s.strip()]

    return {
        'filas': int(config.get('filas', 14)),
        'columnas': int(config.get('columnas', 14)),
        'sillas_bloque': int(config.get('sillas_bloque', 7)),
        'filas_vip': int(config.get('filas_vip', 0)),
        'sillas_inhabilitadas': sillas_inh_lista,
        'sillas_inhabilitadas_texto': texto_limpio_inh
    }

@app.route('/admin')
@app.route('/admin/dashboard')
def admin_dashboard():
    conn = get_db_connection()
    ventas_raw = conn.execute('SELECT * FROM ventas ORDER BY fecha DESC').fetchall()
    combos_db = conn.execute('SELECT * FROM combos').fetchall()
    conn.close()

    config_sala = obtener_config_sala()

    # Se calcula el aforo total usando las 2 naves y restando las sillas inhabilitadas
    sillas_por_nave = config_sala['sillas_bloque'] if config_sala['sillas_bloque'] > 0 else 5
    total_asientos_sala = (config_sala['filas'] * (sillas_por_nave * 2)) - len(config_sala['sillas_inhabilitadas'])

    total_dinero = 0
    total_boletas = 0
    asientos_ocupados = []
    combos_resumen = {}
    ventas_procesadas = []

    for v in ventas_raw:
        total_dinero += v['total']
        
        # Procesar Asientos
        asientos_list = json.loads(v['asientos']) if v['asientos'] else []
        total_boletas += len(asientos_list)
        codigos_asientos = [str(a['codigo']).strip().upper() for a in asientos_list]
        asientos_ocupados.extend(codigos_asientos)

        # Procesar Combos
        combos_list = json.loads(v['combos_json']) if v['combos_json'] else []
        combos_texto_arr = []
        for c in combos_list:
            nombre = c['nombre']
            cant = c['cantidad']
            subtotal = c['subtotal']
            combos_texto_arr.append(f"{nombre} (x{cant})")

            if nombre not in combos_resumen:
                combos_resumen[nombre] = {'cantidad': 0, 'recaudado': 0}
            combos_resumen[nombre]['cantidad'] += cant
            combos_resumen[nombre]['recaudado'] += subtotal

        v_dict = dict(v)
        v_dict['asientos_texto'] = ", ".join(codigos_asientos)
        v_dict['combos_texto'] = ", ".join(combos_texto_arr) if combos_texto_arr else "Ninguno"
        ventas_procesadas.append(v_dict)

    # Generar la estructura dinámica de naves segun sillas_bloque
    num_filas = config_sala['filas']
    num_columnas = config_sala['columnas']
    sillas_bloque = config_sala['sillas_bloque']
    filas_vip = config_sala['filas_vip']
    sillas_inhabilitadas = config_sala['sillas_inhabilitadas']

    sillas_por_nave = sillas_bloque if sillas_bloque > 0 else 5
    cant_naves = 2
    naves = []

    for n_idx in range(cant_naves):
        col_inicio = n_idx * sillas_por_nave
        col_fin = col_inicio + sillas_por_nave
        
        filas_nave = []
        for f_idx in range(num_filas):
            etiqueta_fila = obtener_etiqueta_fila(f_idx)
            
            # Si es la Nave 2 (índice 1), duplicamos la letra (A -> AA, B -> BB)
            if n_idx == 1:
                etiqueta_fila = etiqueta_fila * 2
                
            es_vip = f_idx < filas_vip
            asientos_fila = []

            for c_idx in range(col_inicio, col_fin):
                num_silla_local = c_idx - col_inicio + 1
                # El código de la silla ahora usa el número local (1, 2, 3) en lugar de continuar (6, 7, 8)
                codigo_silla = f"{etiqueta_fila}{num_silla_local}"
                
                estado = 'disponible'
                if codigo_silla in sillas_inhabilitadas:
                    estado = 'inhabilitado'
                elif codigo_silla in asientos_ocupados:
                    estado = 'ocupado'

                asientos_fila.append({
                    'codigo': codigo_silla,
                    'num_silla': num_silla_local,
                    'estado': estado,
                    'es_vip': es_vip
                })

            filas_nave.append({
                'etiqueta': etiqueta_fila,
                'asientos': asientos_fila
            })

        naves.append({
            'numero': n_idx + 1,
            'filas': filas_nave
        })

    return render_template('admin/dashboard.html',
                           ventas=ventas_procesadas,
                           total_dinero=total_dinero,
                           total_boletas=total_boletas,
                           asientos_ocupados=asientos_ocupados,
                           combos_resumen=combos_resumen,
                           combos_db=combos_db,
                           config_sala=config_sala,
                           total_asientos_sala=total_asientos_sala,
                           naves=naves)

@app.route('/admin/configurar_sala', methods=['POST'])
def configurar_sala():
    num_filas = request.form.get('filas', '15')
    num_cols = request.form.get('columnas', '10')
    sillas_bloque = request.form.get('sillas_bloque', '5')
    filas_vip = request.form.get('filas_vip', '3')
    raw_inhabilitadas = request.form.get('sillas_inhabilitadas', '')
    
    # Limpiar adecuadamente el texto antes de guardarlo en la BD
    sillas_inhabilitadas_limp = limpiar_sillas_texto(raw_inhabilitadas)

    conn = get_db_connection()
    conn.execute('INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ("filas", ?)', (num_filas,))
    conn.execute('INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ("columnas", ?)', (num_cols,))
    conn.execute('INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ("sillas_bloque", ?)', (sillas_bloque,))
    conn.execute('INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ("filas_vip", ?)', (filas_vip,))
    conn.execute('INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ("sillas_inhabilitadas", ?)', (sillas_inhabilitadas_limp,))
    conn.commit()
    conn.close()

    flash('Configuración de la sala actualizada correctamente.', 'success')
    return redirect(url_for('admin_dashboard'))

# --- NUEVA RUTA: ELIMINAR VENTA INDIVIDUAL ---
@app.route('/admin/eliminar_venta/<int:venta_id>', methods=['POST'])
def eliminar_venta(venta_id):
    conn = get_db_connection()
    # Borramos únicamente la venta que coincida con el ID presionado
    conn.execute('DELETE FROM ventas WHERE id = ?', (venta_id,))
    conn.commit()
    conn.close()
    
    flash('Venta eliminada exitosamente. Las sillas han sido liberadas.', 'success')
    return redirect(url_for('admin_dashboard'))

# AQUÍ PEGAMOS LA NUEVA RUTA PARA GUARDAR COMBOS
@app.route('/admin/combos/guardar', methods=['POST'])
def guardar_combo():
    nombre = request.form.get('nombre')
    precio = request.form.get('precio')
    descripcion = request.form.get('descripcion')
    imagen = request.files.get('imagen')
    
    nombre_imagen = None
    if imagen and allowed_file(imagen.filename):
        nombre_imagen = secure_filename(imagen.filename)
        imagen.save(os.path.join(app.config['UPLOAD_FOLDER'], nombre_imagen))
    
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO combos (nombre, descripcion, precio, imagen)
        VALUES (?, ?, ?, ?)
    ''', (nombre, descripcion, precio, nombre_imagen))
    conn.commit()
    conn.close()
    
    flash('Combo guardado correctamente.', 'success')
    return redirect(url_for('admin_dashboard'))

# --- NUEVA RUTA: EXPORTAR A EXCEL ---
@app.route('/admin/exportar_excel')
def exportar_excel():
    conn = get_db_connection()
    ventas = conn.execute('SELECT * FROM ventas ORDER BY fecha DESC').fetchall()
    conn.close()

    datos = []
    for v in ventas:
        # Extraemos y traducimos los asientos
        asientos_list = json.loads(v['asientos']) if v['asientos'] else []
        codigos_asientos = [str(a['codigo']).strip().upper() for a in asientos_list]
        asientos_texto = ", ".join(codigos_asientos)

        # Extraemos y traducimos los combos
        combos_list = json.loads(v['combos_json']) if v['combos_json'] else []
        combos_texto_arr = [f"{c['nombre']} (x{c['cantidad']})" for c in combos_list]
        combos_texto = ", ".join(combos_texto_arr) if combos_texto_arr else "Ninguno"

        datos.append({
            'ID Venta': v['id'],
            'Cliente': v['nombre'],
            'Cédula': v['cedula'],
            'Celular': v['celular'],
            'Asientos': asientos_texto,
            'Combos': combos_texto,
            'Código Boleta': v['codigo_boleta'],
            'Total Pagado': v['total'],
            'Método Pago': v['metodo_pago'],
            'Fecha': v['fecha']
        })

    df = pd.DataFrame(datos)
    output = io.BytesIO()
    # Usamos openpyxl para generar el Excel en memoria
    df.to_excel(output, index=False, engine='openpyxl')
    output.seek(0)
    
    return send_file(output, download_name='reporte_ventas.xlsx', as_attachment=True)

# --- NUEVA RUTA: EDITAR COMBO ---
@app.route('/admin/combos/editar/<int:id>', methods=['POST'])
def editar_combo(id):
    nombre = request.form.get('nombre')
    precio = request.form.get('precio')
    descripcion = request.form.get('descripcion')
    imagen = request.files.get('imagen')

    conn = get_db_connection()
    
    if imagen and imagen.filename != '':
        nombre_imagen = secure_filename(imagen.filename)
        imagen.save(os.path.join(app.config['UPLOAD_FOLDER'], nombre_imagen))
        conn.execute('''
            UPDATE combos 
            SET nombre = ?, descripcion = ?, precio = ?, imagen = ? 
            WHERE id = ?
        ''', (nombre, descripcion, precio, nombre_imagen, id))
    else:
        conn.execute('''
            UPDATE combos 
            SET nombre = ?, descripcion = ?, precio = ? 
            WHERE id = ?
        ''', (nombre, descripcion, precio, id))
        
    conn.commit()
    conn.close()

    flash('Combo actualizado correctamente.', 'success')
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    print("\n" + "="*50)
    print("🚀 SERVIDOR INICIADO CORRECTAMENTE")
    print("👉 CAJERO (Ctrl + Clic aquí): http://127.0.0.1:5000")
    print("👉 PANTALLA TV (Desde otro PC): http://0.0.0.0:5000/pantalla_cliente (Reemplaza 0.0.0.0 por tu IP)")
    print("="*50 + "\n")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)