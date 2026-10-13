import sqlite3

# El nombre exacto que usa tu aplicación es database.db
NOMBRE_BASE_DATOS = 'database.db' 

def reiniciar_ventas():
    try:
        conn = sqlite3.connect(NOMBRE_BASE_DATOS)
        # 1. Borramos el historial de todas las ventas
        conn.execute('DELETE FROM ventas')
        # 2. Reiniciamos el contador de la secuencia (el número de boleta) a cero
        conn.execute('DELETE FROM sqlite_sequence WHERE name="ventas"')
        conn.commit()
        conn.close()
        print("\n✅ ¡ÉXITO! La tabla de ventas ha sido vaciada. La sala está lista para una nueva función.\n")
    except Exception as e:
        print(f"\n❌ Error al limpiar la base de datos: {e}\n")

if __name__ == '__main__':
    print("="*50)
    print("⚠️  ADVERTENCIA DE SEGURIDAD  ⚠️")
    print("="*50)
    print("Vas a borrar permanentemente TODAS las boletas vendidas y a desocupar todas las sillas.")
    print("Los combos y la configuración de la sala NO se verán afectados.\n")
    
    confirmacion = input("¿Estás 100% seguro de reiniciar la sala? (escribe 'si' para confirmar): ")
    
    if confirmacion.lower().strip() == 'si':
        reiniciar_ventas()
    else:
        print("\nOperación cancelada. Tus ventas están a salvo.\n")
    try:
        conn = sqlite3.connect(NOMBRE_BASE_DATOS)
        # 1. Borramos el historial de todas las ventas
        conn.execute('DELETE FROM ventas')
        # 2. Reiniciamos el contador de la secuencia (el número de boleta) a cero
        conn.execute('DELETE FROM sqlite_sequence WHERE name="ventas"')
        conn.commit()
        conn.close()
        print("\n✅ ¡ÉXITO! La tabla de ventas ha sido vaciada. La sala está lista para una nueva función.\n")
    except Exception as e:
        print(f"\n❌ Error al limpiar la base de datos: {e}\n")

if __name__ == '__main__':
    print("="*50)
    print("⚠️  ADVERTENCIA DE SEGURIDAD  ⚠️")
    print("="*50)
    print("Vas a borrar permanentemente TODAS las boletas vendidas y a desocupar todas las sillas.")
    print("Los combos y la configuración de la sala NO se verán afectados.\n")
    
    confirmacion = input("¿Estás 100% seguro de reiniciar la sala? (escribe 'si' para confirmar): ")
    
    if confirmacion.lower().strip() == 'si':
        reiniciar_ventas()
    else:
        print("\nOperación cancelada. Tus ventas están a salvo.\n")