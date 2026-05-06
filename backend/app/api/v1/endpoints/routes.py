from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db.session import get_db
from app.core.security import verify_password, create_access_token, decode_token
from app.db.models import Usuario
import json

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> dict:
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token invalido o expirado")
    result = await db.execute(text("SELECT id, username, nombre_completo, rol, activo, puede_ver_trazabilidad, email_notificaciones FROM usuarios WHERE id = :id"), {"id": payload.get("sub")})
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
    await db.execute(text("""
        INSERT INTO log_accesos (id, usuario_id, usuario_nombre, accion, modulo, ip)
        VALUES (gen_random_uuid(), :uid, :nombre, 'login', 'auth', :ip)
    """), {"uid": str(user.id), "nombre": user.nombre_completo, "ip": request.client.host if request else "unknown"})
    await db.commit()
    return {"access_token": token, "token_type": "bearer", "rol": user.rol, "nombre_completo": user.nombre_completo, "id": str(user.id)}

@router.post("/auth/logout")
async def logout(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(text("""
        INSERT INTO log_accesos (id, usuario_id, usuario_nombre, accion, modulo)
        VALUES (gen_random_uuid(), :uid, :nombre, 'logout', 'auth')
    """), {"uid": current_user["id"], "nombre": current_user["nombre_completo"]})
    await db.commit()
    return {"ok": True}

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
    campos = ["nombre_completo", "rol", "area", "activo", "puede_ver_trazabilidad", "email_notificaciones", "observaciones"]
    for campo in campos:
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
    result = await db.execute(text("""
        SELECT usuario_nombre, accion, modulo, ip, created_at
        FROM log_accesos ORDER BY created_at DESC LIMIT 200
    """))
    rows = result.fetchall()
    return [{"usuario": r.usuario_nombre, "accion": r.accion, "modulo": r.modulo, "ip": r.ip, "fecha": r.created_at.isoformat() if r.created_at else None} for r in rows]

@router.get("/admin/trazabilidad")
async def trazabilidad(equipo_id: str = None, fecha_desde: str = None, fecha_hasta: str = None, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if not current_user["puede_ver_trazabilidad"] and current_user["rol"] != "admin":
        raise HTTPException(403, "Sin permiso para ver trazabilidad")
    where, params = ["1=1"], {}
    if equipo_id:
        where.append("o.equipo_id = :eid")
        params["eid"] = equipo_id
    if fecha_desde:
        from datetime import date
        where.append("o.fecha_apertura >= :fd")
        params["fd"] = date.fromisoformat(fecha_desde)
    if fecha_hasta:
        from datetime import date, timedelta
        where.append("o.fecha_apertura <= :fh")
        params["fh"] = date.fromisoformat(fecha_hasta) + timedelta(days=1)
    sql = f"""
        SELECT o.numero, o.estado, o.descripcion, o.vehiculo_traslado,
               o.horometro_apertura, o.horometro_cierre,
               o.fecha_apertura, o.fecha_liberacion, o.fecha_aprobacion_rrhh, o.fecha_cierre,
               o.mecanico_nombre, o.auxiliar_nombre,
               o.rrhh_aprobado, o.panol_aprobado, o.observaciones,
               e.nombre as equipo_nombre, e.codigo_interno as equipo_codigo, e.ubicacion,
               p.nombre as plan_nombre, p.horas_hito,
               u.nombre_completo as planificador_nombre
        FROM ordenes_trabajo o
        LEFT JOIN equipos e ON e.id = o.equipo_id
        LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        LEFT JOIN usuarios u ON u.id = o.planificador_id
        WHERE {' AND '.join(where)}
        ORDER BY o.fecha_apertura DESC LIMIT 500
    """
    result = await db.execute(text(sql), params)
    rows = result.fetchall()
    return [{"numero": r.numero, "estado": r.estado, "descripcion": r.descripcion, "vehiculo": r.vehiculo_traslado, "equipo_nombre": r.equipo_nombre, "equipo_codigo": r.equipo_codigo, "ubicacion": r.ubicacion, "plan_nombre": r.plan_nombre, "horas_hito": r.horas_hito, "horometro_apertura": r.horometro_apertura, "horometro_cierre": r.horometro_cierre, "fecha_apertura": r.fecha_apertura.strftime("%d/%m/%Y %H:%M") if r.fecha_apertura else "-", "fecha_liberacion": r.fecha_liberacion.strftime("%d/%m/%Y %H:%M") if r.fecha_liberacion else "-", "fecha_rrhh": r.fecha_aprobacion_rrhh.strftime("%d/%m/%Y %H:%M") if r.fecha_aprobacion_rrhh else "-", "fecha_cierre": r.fecha_cierre.strftime("%d/%m/%Y %H:%M") if r.fecha_cierre else "-", "planificador": r.planificador_nombre, "mecanico": r.mecanico_nombre, "auxiliar": r.auxiliar_nombre, "rrhh_aprobado": r.rrhh_aprobado, "panol_aprobado": r.panol_aprobado, "observaciones": r.observaciones} for r in rows]
