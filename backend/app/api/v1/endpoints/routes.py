from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db.session import get_db
from app.core.security import verify_password, create_access_token, decode_token
from app.db.models import Usuario
import json, qrcode, io, base64, uuid

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

FRONTEND_URL = "https://bucolic-frangipane-798714.netlify.app"

def generar_qr_base64(codigo_interno: str) -> str:
    from urllib.parse import quote
    url = f"{FRONTEND_URL}/equipo.html?codigo={quote(codigo_interno)}"
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> dict:
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token invalido o expirado")
    result = await db.execute(text("SELECT id, username, nombre_completo, rol, activo, puede_ver_trazabilidad FROM usuarios WHERE id = :id"), {"id": payload.get("sub")})
    user = result.fetchone()
    if not user or not user.activo:
        raise HTTPException(status_code=401, detail="Usuario inactivo o no encontrado")
    return {"id": str(user.id), "username": user.username, "nombre_completo": user.nombre_completo, "rol": user.rol, "puede_ver_trazabilidad": user.puede_ver_trazabilidad}

def require_rol(*roles):
    async def check(current_user: dict = Depends(get_current_user)):
        if current_user["rol"] not in roles and current_user["rol"] != "admin":
            raise HTTPException(status_code=403, detail="Sin permiso para este modulo")
        return current_user
    return check

# ─── AUTH ─────────────────────────────────────────────────────────────────────

@router.post("/auth/login")
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db), request: Request = None):
    result = await db.execute(text("SELECT id, username, nombre_completo, hashed_password, rol, activo FROM usuarios WHERE username = :u"), {"u": form.username})
    user = result.fetchone()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Usuario o contrasena incorrectos")
    if not user.activo:
        raise HTTPException(status_code=401, detail="Usuario inactivo")
    token = create_access_token({"sub": str(user.id), "rol": user.rol})
    try:
        await db.execute(text("""
            INSERT INTO log_accesos (id, usuario_id, usuario_nombre, accion, modulo, ip)
            VALUES (gen_random_uuid(), :uid, :nombre, 'login', 'auth', :ip)
        """), {"uid": str(user.id), "nombre": user.nombre_completo, "ip": request.client.host if request else "unknown"})
        await db.commit()
    except Exception as e:
        print(f"Log acceso error: {e}", flush=True)
    return {"access_token": token, "token_type": "bearer", "rol": user.rol, "nombre_completo": user.nombre_completo, "id": str(user.id)}

@router.post("/auth/logout")
async def logout(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(text("""
        INSERT INTO log_accesos (id, usuario_id, usuario_nombre, accion, modulo)
        VALUES (gen_random_uuid(), :uid, :nombre, 'logout', 'auth')
    """), {"uid": current_user["id"], "nombre": current_user["nombre_completo"]})
    await db.commit()
    return {"ok": True}

# ─── VISTA PÚBLICA QR ─────────────────────────────────────────────────────────

@router.get("/publico/equipo/{codigo_interno}")
async def vista_publica_equipo(codigo_interno: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(text("""
        SELECT nombre, codigo_interno, codigo_sap, marca, modelo, anio, ubicacion, sector
        FROM equipos WHERE codigo_interno = :ci AND activo = true
    """), {"ci": codigo_interno})
    e = result.fetchone()
    if not e:
        raise HTTPException(404, "Equipo no encontrado")
    return {"nombre": e.nombre, "codigo_interno": e.codigo_interno, "codigo_sap": e.codigo_sap,
            "marca": e.marca, "modelo": e.modelo, "anio": e.anio,
            "ubicacion": e.ubicacion, "sector": e.sector}

# ─── VISTA AUTORIZADA QR ──────────────────────────────────────────────────────

@router.get("/equipo/{codigo_interno}/detalle")
async def vista_autorizada_equipo(codigo_interno: str, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    result = await db.execute(text("""
        SELECT e.*, u.nombre_completo AS relevador_nombre
        FROM equipos e LEFT JOIN usuarios u ON u.id = e.relevador_id
        WHERE e.codigo_interno = :ci AND e.activo = true
    """), {"ci": codigo_interno})
    e = result.fetchone()
    if not e:
        raise HTTPException(404, "Equipo no encontrado")

    ultimo_r = await db.execute(text("""
        SELECT o.numero, o.fecha_cierre, o.horometro_apertura, o.horometro_cierre,
               o.mecanico_nombre, o.auxiliar_nombre, o.observaciones_cierre,
               p.nombre AS plan_nombre, p.horas_hito
        FROM ordenes_trabajo o LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        WHERE o.equipo_id = :eid AND o.estado = 'completada'
        ORDER BY o.fecha_cierre DESC LIMIT 1
    """), {"eid": str(e.id)})
    ultimo = ultimo_r.fetchone()

    proximo_r = await db.execute(text("""
        SELECT p.nombre, p.horas_hito, p.horas_alerta FROM planes_mantenimiento p
        WHERE p.equipo_id = :eid AND p.activo = true AND p.eliminado = false
        ORDER BY p.horas_hito ASC LIMIT 1
    """), {"eid": str(e.id)})
    proximo = proximo_r.fetchone()

    ot_activa_r = await db.execute(text("""
        SELECT o.numero, o.estado, o.mecanico_nombre, o.fecha_apertura, p.nombre AS plan_nombre
        FROM ordenes_trabajo o LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        WHERE o.equipo_id = :eid AND o.estado NOT IN ('completada','cancelada')
        ORDER BY o.fecha_apertura DESC LIMIT 1
    """), {"eid": str(e.id)})
    ot_activa = ot_activa_r.fetchone()

    hist_r = await db.execute(text("""
        SELECT o.numero, o.horometro_apertura, o.horometro_cierre,
               o.fecha_apertura, o.fecha_cierre, o.mecanico_nombre, p.nombre AS plan_nombre
        FROM ordenes_trabajo o LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        WHERE o.equipo_id = :eid AND o.estado = 'completada'
        ORDER BY o.fecha_cierre DESC LIMIT 5
    """), {"eid": str(e.id)})
    historial = hist_r.fetchall()

    return {
        "nombre": e.nombre, "codigo_interno": e.codigo_interno, "codigo_sap": e.codigo_sap,
        "marca": e.marca, "modelo": e.modelo, "anio": e.anio,
        "ubicacion": e.ubicacion, "sector": e.sector, "horometro_actual": e.horometro_actual,
        "relevador": e.relevador_nombre,
        "obs_relevador": e.obs_relevador, "obs_horometrista": e.obs_horometrista,
        "obs_operador": e.obs_operador, "obs_planificador": e.obs_planificador,
        "ot_activa": {"numero": ot_activa.numero, "estado": ot_activa.estado,
                      "mecanico": ot_activa.mecanico_nombre, "plan": ot_activa.plan_nombre,
                      "fecha_apertura": ot_activa.fecha_apertura.strftime("%d/%m/%Y %H:%M") if ot_activa.fecha_apertura else "—"} if ot_activa else None,
        "ultimo_servicio": {"numero": ultimo.numero, "plan": ultimo.plan_nombre,
                            "horas_hito": ultimo.horas_hito,
                            "horometro_apertura": ultimo.horometro_apertura,
                            "horometro_cierre": ultimo.horometro_cierre,
                            "mecanico": ultimo.mecanico_nombre, "auxiliar": ultimo.auxiliar_nombre,
                            "fecha_cierre": ultimo.fecha_cierre.strftime("%d/%m/%Y %H:%M") if ultimo.fecha_cierre else "—",
                            "observaciones": ultimo.observaciones_cierre} if ultimo else None,
        "proximo_servicio": {"plan": proximo.nombre, "horas_hito": proximo.horas_hito,
                             "horas_alerta": proximo.horas_alerta,
                             "hs_faltantes": round(proximo.horas_hito - (e.horometro_actual or 0), 1)} if proximo else None,
        "historial": [{"numero": h.numero, "plan": h.plan_nombre,
                       "horometro_apertura": h.horometro_apertura, "horometro_cierre": h.horometro_cierre,
                       "mecanico": h.mecanico_nombre,
                       "fecha_apertura": h.fecha_apertura.strftime("%d/%m/%Y") if h.fecha_apertura else "—",
                       "fecha_cierre": h.fecha_cierre.strftime("%d/%m/%Y") if h.fecha_cierre else "—"} for h in historial],
    }

# ─── ADMIN ────────────────────────────────────────────────────────────────────

@router.get("/admin/empresa")
async def get_empresa(db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("admin"))):
    result = await db.execute(text("SELECT * FROM empresa_config WHERE id = 1"))
    row = result.fetchone()
    if not row:
        return {"nombre": "Mi Empresa", "logo_base64": None, "logo_mime": None, "cuit": None, "direccion": None, "telefono": None, "email": None, "web": None}
    return {"nombre": row.nombre, "logo_base64": row.logo_base64, "logo_mime": row.logo_mime, "cuit": row.cuit, "direccion": row.direccion, "telefono": row.telefono, "email": row.email, "web": row.web}

@router.put("/admin/empresa")
async def update_empresa(payload: dict, db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("admin"))):
    result = await db.execute(text("SELECT id FROM empresa_config WHERE id = 1"))
    existe = result.fetchone()
    if existe:
        await db.execute(text("""
            UPDATE empresa_config SET nombre=:nombre, logo_base64=:logo, logo_mime=:mime,
            cuit=:cuit, direccion=:dir, telefono=:tel, email=:email, web=:web WHERE id=1
        """), {"nombre": payload.get("nombre"), "logo": payload.get("logo_base64"), "mime": payload.get("logo_mime"), "cuit": payload.get("cuit"), "dir": payload.get("direccion"), "tel": payload.get("telefono"), "email": payload.get("email"), "web": payload.get("web")})
    else:
        await db.execute(text("""
            INSERT INTO empresa_config (id, nombre, logo_base64, logo_mime, cuit, direccion, telefono, email, web)
            VALUES (1, :nombre, :logo, :mime, :cuit, :dir, :tel, :email, :web)
        """), {"nombre": payload.get("nombre"), "logo": payload.get("logo_base64"), "mime": payload.get("logo_mime"), "cuit": payload.get("cuit"), "dir": payload.get("direccion"), "tel": payload.get("telefono"), "email": payload.get("email"), "web": payload.get("web")})
    await db.commit()
    return {"ok": True}

@router.get("/admin/usuarios")
async def listar_usuarios(db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("admin"))):
    result = await db.execute(text("""
        SELECT id, username, nombre_completo, rol, area, activo, puede_ver_trazabilidad, email_notificaciones, observaciones
        FROM usuarios ORDER BY nombre_completo
    """))
    rows = result.fetchall()
    return [{"id": str(r.id), "username": r.username, "nombre_completo": r.nombre_completo, "rol": r.rol, "area": r.area, "activo": r.activo, "puede_ver_trazabilidad": r.puede_ver_trazabilidad, "email_notificaciones": r.email_notificaciones, "observaciones": r.observaciones} for r in rows]

@router.post("/admin/usuarios")
async def crear_usuario(payload: dict, db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("admin"))):
    from app.core.security import hash_password
    existe = await db.execute(text("SELECT id FROM usuarios WHERE username = :u"), {"u": payload["username"]})
    if existe.fetchone():
        raise HTTPException(400, "El usuario ya existe")
    await db.execute(text("""
        INSERT INTO usuarios (id, username, nombre_completo, email, hashed_password, rol, area, activo, puede_ver_trazabilidad, email_notificaciones, observaciones)
        VALUES (gen_random_uuid(), :username, :nombre, :email, :pwd, :rol, :area, :activo, :pvt, :email_noti, :obs)
    """), {"username": payload["username"], "nombre": payload["nombre_completo"], "email": payload.get("email"), "pwd": hash_password(payload.get("password", "manten1234")), "rol": payload["rol"], "area": payload.get("area"), "activo": payload.get("activo", True), "pvt": payload.get("puede_ver_trazabilidad", False), "email_noti": payload.get("email_notificaciones"), "obs": payload.get("observaciones")})
    await db.commit()
    return {"ok": True}

@router.put("/admin/usuarios/{user_id}")
async def editar_usuario(user_id: str, payload: dict, db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("admin"))):
    from app.core.security import hash_password
    sets, params = [], {"id": user_id}
    for campo in ["nombre_completo", "rol", "area", "activo", "puede_ver_trazabilidad", "email_notificaciones", "observaciones"]:
        if campo in payload:
            sets.append(f"{campo} = :{campo}")
            params[campo] = payload[campo]
    if "password" in payload and payload["password"]:
        sets.append("hashed_password = :pwd")
        params["pwd"] = hash_password(payload["password"])
    if sets:
        await db.execute(text(f"UPDATE usuarios SET {', '.join(sets)} WHERE id = :id"), params)
        await db.commit()
    return {"ok": True}

@router.get("/admin/log-accesos")
async def log_accesos(db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("admin"))):
    result = await db.execute(text("SELECT usuario_nombre, accion, modulo, ip, created_at FROM log_accesos ORDER BY created_at DESC LIMIT 200"))
    rows = result.fetchall()
    return [{"usuario": r.usuario_nombre, "accion": r.accion, "modulo": r.modulo, "ip": r.ip, "fecha": r.created_at.isoformat() if r.created_at else None} for r in rows]

@router.get("/admin/trazabilidad")
async def trazabilidad(equipo_id: str = None, fecha_desde: str = None, fecha_hasta: str = None, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if not current_user["puede_ver_trazabilidad"] and current_user["rol"] != "admin":
        raise HTTPException(403, "Sin permiso para ver trazabilidad")
    where, params = ["1=1"], {}
    if equipo_id:
        where.append("o.equipo_id = :eid"); params["eid"] = equipo_id
    if fecha_desde:
        from datetime import date
        where.append("o.fecha_apertura >= :fd"); params["fd"] = date.fromisoformat(fecha_desde)
    if fecha_hasta:
        from datetime import date, timedelta
        where.append("o.fecha_apertura <= :fh"); params["fh"] = date.fromisoformat(fecha_hasta) + timedelta(days=1)
    result = await db.execute(text(f"""
        SELECT o.numero, o.estado, o.descripcion, o.vehiculo_traslado,
               o.horometro_apertura, o.horometro_cierre,
               o.fecha_apertura, o.fecha_liberacion, o.fecha_aprobacion_rrhh, o.fecha_cierre,
               o.mecanico_nombre, o.auxiliar_nombre, o.rrhh_aprobado, o.panol_aprobado, o.observaciones,
               e.nombre as equipo_nombre, e.codigo_interno as equipo_codigo, e.ubicacion,
               p.nombre as plan_nombre, p.horas_hito, u.nombre_completo as planificador_nombre
        FROM ordenes_trabajo o
        LEFT JOIN equipos e ON e.id = o.equipo_id
        LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        LEFT JOIN usuarios u ON u.id = o.planificador_id
        WHERE {' AND '.join(where)} ORDER BY o.fecha_apertura DESC LIMIT 500
    """), params)
    rows = result.fetchall()
    return [{"numero": r.numero, "estado": r.estado, "descripcion": r.descripcion, "vehiculo": r.vehiculo_traslado, "equipo_nombre": r.equipo_nombre, "equipo_codigo": r.equipo_codigo, "ubicacion": r.ubicacion, "plan_nombre": r.plan_nombre, "horas_hito": r.horas_hito, "horometro_apertura": r.horometro_apertura, "horometro_cierre": r.horometro_cierre, "fecha_apertura": r.fecha_apertura.strftime("%d/%m/%Y %H:%M") if r.fecha_apertura else "-", "fecha_liberacion": r.fecha_liberacion.strftime("%d/%m/%Y %H:%M") if r.fecha_liberacion else "-", "fecha_rrhh": r.fecha_aprobacion_rrhh.strftime("%d/%m/%Y %H:%M") if r.fecha_aprobacion_rrhh else "-", "fecha_cierre": r.fecha_cierre.strftime("%d/%m/%Y %H:%M") if r.fecha_cierre else "-", "planificador": r.planificador_nombre, "mecanico": r.mecanico_nombre, "auxiliar": r.auxiliar_nombre, "rrhh_aprobado": r.rrhh_aprobado, "panol_aprobado": r.panol_aprobado, "observaciones": r.observaciones} for r in rows]

# ─── RELEVADOR ────────────────────────────────────────────────────────────────

@router.get("/relevador/equipos")
async def relevador_equipos(db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("relevador"))):
    result = await db.execute(text("""
        SELECT id, nombre, codigo_interno, codigo_sap, ubicacion, sector,
               marca, modelo, anio, horometro_actual, activo, observaciones,
               obs_relevador, foto1_base64, qr_code
        FROM equipos ORDER BY nombre
    """))
    rows = result.fetchall()
    return [{"id": str(r.id), "nombre": r.nombre, "codigo_interno": r.codigo_interno,
             "codigo_sap": r.codigo_sap, "ubicacion": r.ubicacion, "sector": r.sector,
             "marca": r.marca, "modelo": r.modelo, "anio": r.anio,
             "horometro_actual": r.horometro_actual, "activo": r.activo,
             "observaciones": r.observaciones, "obs_relevador": r.obs_relevador,
             "foto1_base64": r.foto1_base64, "qr_code": r.qr_code} for r in rows]

@router.post("/relevador/equipos")
async def relevador_crear_equipo(payload: dict, db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("relevador"))):
    existe = await db.execute(text("SELECT id FROM equipos WHERE codigo_interno = :c"), {"c": payload["codigo_interno"]})
    if existe.fetchone():
        raise HTTPException(400, "Código interno ya existe")
    eid = str(uuid.uuid4())
    qr_base64 = generar_qr_base64(payload["codigo_interno"])
    await db.execute(text("""
        INSERT INTO equipos (id, nombre, codigo_interno, codigo_sap, ubicacion, sector,
                             marca, modelo, anio, horometro_inicial, horometro_actual,
                             foto1_base64, qr_code, activo, observaciones, obs_relevador, relevador_id)
        VALUES (:id, :nombre, :ci, :cs, :ubi, :sector, :marca, :modelo, :anio,
                :h0, :h0, :foto, :qr, :activo, :obs, :obs_rel, :rid)
    """), {"id": eid, "nombre": payload["nombre"], "ci": payload["codigo_interno"],
           "cs": payload.get("codigo_sap"), "ubi": payload.get("ubicacion"),
           "sector": payload.get("sector"), "marca": payload.get("marca"),
           "modelo": payload.get("modelo"), "anio": payload.get("anio"),
           "h0": payload.get("horometro_inicial", 0), "foto": payload.get("foto1_base64"),
           "qr": qr_base64, "activo": payload.get("activo", True),
           "obs": payload.get("observaciones"), "obs_rel": payload.get("obs_relevador"),
           "rid": current_user["id"]})
    await db.commit()
    return {"ok": True, "id": eid}

@router.put("/relevador/equipos/{equipo_id}")
async def relevador_editar_equipo(equipo_id: str, payload: dict, db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("relevador"))):
    result = await db.execute(text("SELECT codigo_interno FROM equipos WHERE id = :id"), {"id": equipo_id})
    equipo = result.fetchone()
    codigo = payload.get("codigo_interno", equipo.codigo_interno if equipo else None)
    data = dict(payload)
    if codigo:
        data["qr_code"] = generar_qr_base64(codigo)
    sets, params = [], {"id": equipo_id}
    for campo in ["nombre", "codigo_sap", "ubicacion", "sector", "marca", "modelo", "anio",
                  "activo", "observaciones", "obs_relevador", "foto1_base64", "qr_code"]:
        if campo in data:
            sets.append(f"{campo} = :{campo}")
            params[campo] = data[campo]
    if sets:
        await db.execute(text(f"UPDATE equipos SET {', '.join(sets)} WHERE id = :id"), params)
        await db.commit()
    return {"ok": True}

# ─── HOROMETRISTA ─────────────────────────────────────────────────────────────

@router.get("/horometrista/equipos")
async def horometrista_equipos(db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("horometrista"))):
    result = await db.execute(text("""
        SELECT id, nombre, codigo_interno, ubicacion, sector, horometro_actual, activo, obs_horometrista
        FROM equipos WHERE activo = true ORDER BY nombre
    """))
    rows = result.fetchall()
    return [{"id": str(r.id), "nombre": r.nombre, "codigo_interno": r.codigo_interno,
             "ubicacion": r.ubicacion, "sector": r.sector,
             "horometro_actual": r.horometro_actual,
             "obs_horometrista": r.obs_horometrista} for r in rows]

@router.post("/horometrista/horometros")
async def cargar_horometro(payload: dict, db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("horometrista"))):
    result = await db.execute(text("SELECT horometro_actual FROM equipos WHERE id = :id"), {"id": payload["equipo_id"]})
    equipo = result.fetchone()
    if not equipo:
        raise HTTPException(404, "Equipo no encontrado")
    await db.execute(text("""
        INSERT INTO horometros (id, equipo_id, usuario_id, lectura, lectura_anterior, observaciones)
        VALUES (:id, :eid, :uid, :lec, :ant, :obs)
    """), {"id": str(uuid.uuid4()), "eid": payload["equipo_id"], "uid": current_user["id"],
           "lec": payload["lectura"], "ant": equipo.horometro_actual,
           "obs": payload.get("observaciones")})
    await db.execute(text("UPDATE equipos SET horometro_actual = :lec WHERE id = :id"),
                     {"lec": payload["lectura"], "id": payload["equipo_id"]})
    if payload.get("obs_horometrista") is not None:
        await db.execute(text("UPDATE equipos SET obs_horometrista = :obs WHERE id = :id"),
                         {"obs": payload.get("obs_horometrista"), "id": payload["equipo_id"]})
    await db.commit()
    return {"ok": True}

@router.get("/horometrista/horometros/{equipo_id}")
async def historial_horometro(equipo_id: str, db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("horometrista"))):
    result = await db.execute(text("""
        SELECT h.lectura, h.lectura_anterior, h.fecha, h.observaciones, u.nombre_completo
        FROM horometros h LEFT JOIN usuarios u ON u.id = h.usuario_id
        WHERE h.equipo_id = :eid ORDER BY h.fecha DESC LIMIT 50
    """), {"eid": equipo_id})
    rows = result.fetchall()
    return [{"lectura": r.lectura, "anterior": r.lectura_anterior,
             "fecha": r.fecha.strftime("%d/%m/%Y %H:%M") if r.fecha else "-",
             "operador": r.nombre_completo, "obs": r.observaciones} for r in rows]

@router.put("/horometrista/horometros/{equipo_id}/corregir")
async def corregir_horometro(equipo_id: str, payload: dict, db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_rol("horometrista"))):
    result = await db.execute(text("SELECT id, lectura FROM horometros WHERE equipo_id = :eid ORDER BY fecha DESC LIMIT 1"), {"eid": equipo_id})
    ultima = result.fetchone()
    if not ultima:
        raise HTTPException(404, "No hay lecturas para corregir")
    await db.execute(text("""
        INSERT INTO horometros (id, equipo_id, usuario_id, lectura, lectura_anterior, es_correccion, lectura_original, motivo_correccion, observaciones)
        VALUES (:id, :eid, :uid, :lec, :ant, true, :orig, :motivo, :obs)
    """), {"id": str(uuid.uuid4()), "eid": equipo_id, "uid": current_user["id"],
           "lec": payload["lectura"], "ant": ultima.lectura, "orig": ultima.lectura,
           "motivo": payload.get("motivo", "Correccion manual"), "obs": payload.get("observaciones")})
    await db.execute(text("UPDATE equipos SET horometro_actual = :lec WHERE id = :id"),
                     {"lec": payload["lectura"], "id": equipo_id})
    await db.commit()
    return {"ok": True}

# ─── OBSERVACIONES POR ROL ────────────────────────────────────────────────────

@router.put("/equipo/{equipo_id}/observacion")
async def guardar_observacion(equipo_id: str, payload: dict, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    campo_map = {"relevador": "obs_relevador", "horometrista": "obs_horometrista",
                 "operario": "obs_operador", "planificador": "obs_planificador", "admin": "obs_planificador"}
    campo = campo_map.get(current_user["rol"])
    if not campo:
        raise HTTPException(403, "Sin permiso para agregar observaciones")
    await db.execute(text(f"UPDATE equipos SET {campo} = :obs WHERE id = :id"),
                     {"obs": payload.get("observacion"), "id": equipo_id})
    await db.commit()
    return {"ok": True}
