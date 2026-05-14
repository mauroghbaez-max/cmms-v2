from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db.session import get_db
from app.api.v1.endpoints.routes import get_current_user, require_rol
from datetime import datetime, date
import uuid

router = APIRouter()

# ─── REPORTE HORÓMETROS ───────────────────────────────────────────────────────

@router.get("/planificador/reporte-horometros")
async def reporte_horometros(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    """
    Reporte principal: todos los equipos con sus planes activos,
    horas acumuladas desde el último servicio y semáforo.
    """
    sql = """
        SELECT
            e.id            AS equipo_id,
            e.nombre        AS equipo_nombre,
            e.codigo_interno,
            e.horometro_actual,
            p.id            AS plan_id,
            p.nombre        AS plan_nombre,
            p.horas_hito,
            p.horas_alerta,
            -- Horómetro al cierre del último servicio de este plan
            (
                SELECT o.horometro_cierre
                FROM ordenes_trabajo o
                WHERE o.equipo_id = e.id
                  AND o.plan_id   = p.id
                  AND o.estado    = 'completada'
                  AND o.horometro_cierre IS NOT NULL
                ORDER BY o.fecha_cierre DESC
                LIMIT 1
            ) AS horometro_ultimo_servicio,
            (
                SELECT o.fecha_cierre
                FROM ordenes_trabajo o
                WHERE o.equipo_id = e.id
                  AND o.plan_id   = p.id
                  AND o.estado    = 'completada'
                ORDER BY o.fecha_cierre DESC
                LIMIT 1
            ) AS fecha_ultimo_servicio,
            -- OT activa para este plan (para evitar duplicados)
            (
                SELECT o.numero
                FROM ordenes_trabajo o
                WHERE o.equipo_id = e.id
                  AND o.plan_id   = p.id
                  AND o.estado NOT IN ('completada', 'cancelada')
                ORDER BY o.fecha_apertura DESC
                LIMIT 1
            ) AS ot_activa_numero
        FROM equipos e
        JOIN planes_mantenimiento p ON p.equipo_id = e.id
        WHERE e.activo = true
          AND p.activo = true
          AND p.eliminado = false
        ORDER BY e.nombre, p.horas_hito
    """
    result = await db.execute(text(sql))
    rows = result.fetchall()

    data = []
    for r in rows:
        h_actual = r.horometro_actual or 0
        h_ultimo = r.horometro_ultimo_servicio if r.horometro_ultimo_servicio is not None else 0
        hs_acumuladas = h_actual - h_ultimo
        hs_faltantes  = r.horas_hito - hs_acumuladas

        if hs_faltantes <= 0:
            semaforo = "rojo"
        elif hs_faltantes <= (r.horas_alerta or 150):
            semaforo = "amarillo"
        else:
            semaforo = "verde"

        data.append({
            "equipo_id":               str(r.equipo_id),
            "equipo_nombre":           r.equipo_nombre,
            "codigo_interno":          r.codigo_interno,
            "horometro_actual":        h_actual,
            "plan_id":                 str(r.plan_id),
            "plan_nombre":             r.plan_nombre,
            "horas_hito":              r.horas_hito,
            "horas_alerta":            r.horas_alerta or 150,
            "horometro_ultimo_servicio": h_ultimo,
            "fecha_ultimo_servicio":   r.fecha_ultimo_servicio.strftime("%d/%m/%Y") if r.fecha_ultimo_servicio else "—",
            "hs_acumuladas":           round(hs_acumuladas, 1),
            "hs_faltantes":            round(hs_faltantes, 1),
            "semaforo":                semaforo,
            "ot_activa_numero":        r.ot_activa_numero,
        })
    return data


@router.get("/planificador/historial-servicios/{equipo_id}")
async def historial_servicios(
    equipo_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    result = await db.execute(text("""
        SELECT o.numero, o.tipo, o.estado, o.horometro_apertura, o.horometro_cierre,
               o.fecha_apertura, o.fecha_cierre, o.descripcion,
               p.nombre AS plan_nombre, p.horas_hito
        FROM ordenes_trabajo o
        LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        WHERE o.equipo_id = :eid
        ORDER BY o.fecha_apertura DESC
        LIMIT 100
    """), {"eid": equipo_id})
    rows = result.fetchall()
    return [{
        "numero":             r.numero,
        "tipo":               r.tipo,
        "estado":             r.estado,
        "plan_nombre":        r.plan_nombre or "—",
        "horas_hito":         r.horas_hito,
        "horometro_apertura": r.horometro_apertura,
        "horometro_cierre":   r.horometro_cierre,
        "fecha_apertura":     r.fecha_apertura.strftime("%d/%m/%Y %H:%M") if r.fecha_apertura else "—",
        "fecha_cierre":       r.fecha_cierre.strftime("%d/%m/%Y %H:%M") if r.fecha_cierre else "—",
        "descripcion":        r.descripcion or "—",
    } for r in rows]


# ─── PLANES DE MANTENIMIENTO ──────────────────────────────────────────────────

@router.get("/planificador/equipos")
async def planificador_equipos(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    result = await db.execute(text("""
        SELECT id, nombre, codigo_interno, codigo_sap, marca, modelo,
               ubicacion, sector, horometro_actual, activo
        FROM equipos ORDER BY nombre
    """))
    rows = result.fetchall()
    return [{"id": str(r.id), "nombre": r.nombre,
             "codigo_interno": r.codigo_interno,
             "codigo_sap": r.codigo_sap,
             "marca": r.marca, "modelo": r.modelo,
             "ubicacion": r.ubicacion, "sector": r.sector,
             "horometro_actual": r.horometro_actual,
             "activo": r.activo} for r in rows]


@router.get("/planificador/planes/{equipo_id}")
async def listar_planes(
    equipo_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    result = await db.execute(text("""
        SELECT p.id, p.nombre, p.horas_hito, p.horas_alerta, p.cantidad_personal,
               p.observaciones, p.activo, p.eliminado,
               p.modificado_por_nombre, p.motivo_modificacion, p.fecha_modificacion
        FROM planes_mantenimiento p
        WHERE p.equipo_id = :eid AND p.eliminado = false
        ORDER BY p.horas_hito
    """), {"eid": equipo_id})
    rows = result.fetchall()
    planes = []
    for r in rows:
        materiales_r = await db.execute(text("""
            SELECT id, codigo_sap, descripcion, cantidad, unidad
            FROM plan_materiales WHERE plan_id = :pid ORDER BY descripcion
        """), {"pid": str(r.id)})
        mats = materiales_r.fetchall()
        planes.append({
            "id": str(r.id), "nombre": r.nombre,
            "horas_hito": r.horas_hito, "horas_alerta": r.horas_alerta,
            "cantidad_personal": r.cantidad_personal,
            "observaciones": r.observaciones, "activo": r.activo,
            "modificado_por": r.modificado_por_nombre,
            "motivo_modificacion": r.motivo_modificacion,
            "fecha_modificacion": r.fecha_modificacion.strftime("%d/%m/%Y") if r.fecha_modificacion else None,
            "materiales": [{"id": str(m.id), "codigo_sap": m.codigo_sap,
                            "descripcion": m.descripcion,
                            "cantidad": m.cantidad, "unidad": m.unidad} for m in mats],
        })
    return planes


@router.post("/planificador/planes")
async def crear_plan(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    # Validar que no exista otro plan con la misma horas_hito para el equipo
    existe = await db.execute(text("""
        SELECT id FROM planes_mantenimiento
        WHERE equipo_id = :eid AND horas_hito = :hh AND eliminado = false
    """), {"eid": payload["equipo_id"], "hh": payload["horas_hito"]})
    if existe.fetchone():
        raise HTTPException(400, "Ya existe un plan con esas horas para este equipo")

    plan_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO planes_mantenimiento
            (id, equipo_id, nombre, horas_hito, horas_alerta, cantidad_personal, observaciones)
        VALUES (:id, :eid, :nombre, :hh, :ha, :cp, :obs)
    """), {
        "id": plan_id, "eid": payload["equipo_id"],
        "nombre": payload["nombre"], "hh": payload["horas_hito"],
        "ha": payload.get("horas_alerta", 150),
        "cp": payload.get("cantidad_personal", 1),
        "obs": payload.get("observaciones"),
    })

    for mat in payload.get("materiales", []):
        await db.execute(text("""
            INSERT INTO plan_materiales (id, plan_id, codigo_sap, descripcion, cantidad, unidad)
            VALUES (gen_random_uuid(), :pid, :cs, :desc, :cant, :un)
        """), {"pid": plan_id, "cs": mat.get("codigo_sap"),
               "desc": mat["descripcion"], "cant": mat.get("cantidad", 1),
               "un": mat.get("unidad", "UN")})
    await db.commit()
    return {"ok": True, "id": plan_id}


@router.put("/planificador/planes/{plan_id}")
async def modificar_plan(
    plan_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    # Validar que tenga OT activa (no se puede modificar si tiene OT)
    ot_activa = await db.execute(text("""
        SELECT id FROM ordenes_trabajo
        WHERE plan_id = :pid AND estado NOT IN ('completada','cancelada')
        LIMIT 1
    """), {"pid": plan_id})
    if ot_activa.fetchone():
        raise HTTPException(400, "No se puede modificar un plan con OT activa")

    if not payload.get("motivo"):
        raise HTTPException(400, "El motivo de modificación es obligatorio")

    await db.execute(text("""
        UPDATE planes_mantenimiento SET
            nombre = :nombre, horas_hito = :hh, horas_alerta = :ha,
            cantidad_personal = :cp, observaciones = :obs,
            modificado_por_id = :uid, modificado_por_nombre = :unombre,
            motivo_modificacion = :motivo,
            fecha_modificacion = now()
        WHERE id = :id
    """), {
        "nombre": payload["nombre"], "hh": payload["horas_hito"],
        "ha": payload.get("horas_alerta", 150), "cp": payload.get("cantidad_personal", 1),
        "obs": payload.get("observaciones"), "uid": current_user["id"],
        "unombre": current_user["nombre_completo"],
        "motivo": payload["motivo"], "id": plan_id,
    })

    # Reemplazar materiales
    await db.execute(text("DELETE FROM plan_materiales WHERE plan_id = :pid"), {"pid": plan_id})
    for mat in payload.get("materiales", []):
        await db.execute(text("""
            INSERT INTO plan_materiales (id, plan_id, codigo_sap, descripcion, cantidad, unidad)
            VALUES (gen_random_uuid(), :pid, :cs, :desc, :cant, :un)
        """), {"pid": plan_id, "cs": mat.get("codigo_sap"),
               "desc": mat["descripcion"], "cant": mat.get("cantidad", 1),
               "un": mat.get("unidad", "UN")})

    # Registrar en cambios_registro
    await db.execute(text("""
        INSERT INTO cambios_registro (id, tabla, registro_id, usuario_id, usuario_nombre, campo, motivo)
        VALUES (gen_random_uuid(), 'planes_mantenimiento', :rid, :uid, :unombre, 'modificacion', :motivo)
    """), {"rid": plan_id, "uid": current_user["id"],
           "unombre": current_user["nombre_completo"], "motivo": payload["motivo"]})
    await db.commit()
    return {"ok": True}


@router.delete("/planificador/planes/{plan_id}")
async def eliminar_plan(
    plan_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    # No se puede eliminar si tiene OT activa
    ot_activa = await db.execute(text("""
        SELECT id FROM ordenes_trabajo
        WHERE plan_id = :pid AND estado NOT IN ('completada','cancelada')
        LIMIT 1
    """), {"pid": plan_id})
    if ot_activa.fetchone():
        raise HTTPException(400, "No se puede eliminar un plan con OT activa")

    if not payload.get("motivo"):
        raise HTTPException(400, "El motivo de eliminación es obligatorio")

    await db.execute(text("""
        UPDATE planes_mantenimiento SET
            eliminado = true,
            motivo_eliminacion = :motivo,
            eliminado_por_nombre = :unombre,
            fecha_eliminacion = now()
        WHERE id = :id
    """), {"motivo": payload["motivo"],
           "unombre": current_user["nombre_completo"], "id": plan_id})

    await db.execute(text("""
        INSERT INTO cambios_registro (id, tabla, registro_id, usuario_id, usuario_nombre, campo, motivo)
        VALUES (gen_random_uuid(), 'planes_mantenimiento', :rid, :uid, :unombre, 'eliminacion', :motivo)
    """), {"rid": plan_id, "uid": current_user["id"],
           "unombre": current_user["nombre_completo"], "motivo": payload["motivo"]})
    await db.commit()
    return {"ok": True}


# ─── ÓRDENES DE TRABAJO ───────────────────────────────────────────────────────

@router.get("/planificador/ots")
async def listar_ots(
    estado: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    where = "1=1"
    params = {}
    if estado:
        where += " AND o.estado = :estado"
        params["estado"] = estado

    result = await db.execute(text(f"""
        SELECT o.id, o.numero, o.tipo, o.urgente, o.estado,
               o.descripcion, o.rrhh_aprobado, o.liberada_por_planificador,
               o.fecha_apertura, o.fecha_cierre, o.horometro_apertura,
               e.nombre AS equipo_nombre, e.codigo_interno,
               p.nombre AS plan_nombre, p.horas_hito
        FROM ordenes_trabajo o
        LEFT JOIN equipos e ON e.id = o.equipo_id
        LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        WHERE {where}
        ORDER BY o.fecha_apertura DESC
        LIMIT 200
    """), params)
    rows = result.fetchall()
    return [{
        "id": str(r.id), "numero": r.numero, "tipo": r.tipo,
        "urgente": r.urgente, "estado": r.estado,
        "descripcion": r.descripcion,
        "rrhh_aprobado": r.rrhh_aprobado,
        "liberada_por_planificador": r.liberada_por_planificador,
        "equipo_nombre": r.equipo_nombre, "codigo_interno": r.codigo_interno,
        "plan_nombre": r.plan_nombre or "—", "horas_hito": r.horas_hito,
        "horometro_apertura": r.horometro_apertura,
        "fecha_apertura": r.fecha_apertura.strftime("%d/%m/%Y %H:%M") if r.fecha_apertura else "—",
        "fecha_cierre": r.fecha_cierre.strftime("%d/%m/%Y %H:%M") if r.fecha_cierre else "—",
    } for r in rows]


@router.post("/planificador/ots")
async def crear_ot(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    # Generar número correlativo
    result = await db.execute(text("SELECT COUNT(*) FROM ordenes_trabajo"))
    count = result.scalar() or 0
    numero = f"OT-{str(count + 1).zfill(5)}"

    ot_id = str(uuid.uuid4())
    equipo_r = await db.execute(text("SELECT horometro_actual FROM equipos WHERE id = :id"),
                                {"id": payload["equipo_id"]})
    equipo = equipo_r.fetchone()
    horometro_apertura = equipo.horometro_actual if equipo else None

    await db.execute(text("""
        INSERT INTO ordenes_trabajo
            (id, numero, equipo_id, plan_id, planificador_id,
             tipo, urgente, solicitud_correctiva_id,
             descripcion, vehiculo_traslado, horometro_apertura, estado)
        VALUES
            (:id, :num, :eid, :pid, :plid,
             :tipo, :urgente, :sol_id,
             :desc, :veh, :hap, 'pendiente_rrhh')
    """), {
        "id": ot_id, "num": numero,
        "eid": payload["equipo_id"],
        "pid": payload.get("plan_id"),
        "plid": current_user["id"],
        "tipo": payload.get("tipo", "preventiva"),
        "urgente": payload.get("urgente", False),
        "sol_id": payload.get("solicitud_correctiva_id"),
        "desc": payload.get("descripcion"),
        "veh": payload.get("vehiculo_traslado"),
        "hap": horometro_apertura,
    })

    # Copiar materiales del plan si es preventiva
    if payload.get("plan_id"):
        mats = await db.execute(text("""
            SELECT codigo_sap, descripcion, cantidad, unidad
            FROM plan_materiales WHERE plan_id = :pid
        """), {"pid": payload["plan_id"]})
        for m in mats.fetchall():
            await db.execute(text("""
                INSERT INTO ot_materiales
                    (id, ot_id, codigo_sap, descripcion, cantidad, unidad,
                     agregado_por_id, agregado_por_nombre)
                VALUES (gen_random_uuid(), :oid, :cs, :desc, :cant, :un, :uid, :unombre)
            """), {"oid": ot_id, "cs": m.codigo_sap, "desc": m.descripcion,
                   "cant": m.cantidad, "un": m.unidad,
                   "uid": current_user["id"],
                   "unombre": current_user["nombre_completo"]})

    # Agregar materiales extra si vienen en el payload
    for mat in payload.get("materiales_extra", []):
        await db.execute(text("""
            INSERT INTO ot_materiales
                (id, ot_id, codigo_sap, descripcion, cantidad, unidad,
                 tipo, agregado_por_id, agregado_por_nombre)
            VALUES (gen_random_uuid(), :oid, :cs, :desc, :cant, :un,
                    'extra', :uid, :unombre)
        """), {"oid": ot_id, "cs": mat.get("codigo_sap"),
               "desc": mat["descripcion"], "cant": mat.get("cantidad", 1),
               "un": mat.get("unidad", "UN"),
               "uid": current_user["id"],
               "unombre": current_user["nombre_completo"]})

    # Si la solicitud correctiva existía, marcarla como aprobada
    if payload.get("solicitud_correctiva_id"):
        await db.execute(text("""
            UPDATE solicitudes_correctivas SET estado = 'aprobada'
            WHERE id = :sid
        """), {"sid": payload["solicitud_correctiva_id"]})

    await db.commit()
    return {"ok": True, "id": ot_id, "numero": numero}


@router.post("/planificador/ots/{ot_id}/imprimir")
async def imprimir_ot(
    ot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    """
    Marca la OT como impresa y libera la orden de materiales al Pañol.
    Solo se puede imprimir si RRHH ya dio el visto bueno.
    """
    ot_r = await db.execute(text("""
        SELECT id, rrhh_aprobado, liberada_por_planificador, estado
        FROM ordenes_trabajo WHERE id = :id
    """), {"id": ot_id})
    ot = ot_r.fetchone()
    if not ot:
        raise HTTPException(404, "OT no encontrada")
    if not ot.rrhh_aprobado:
        raise HTTPException(400, "RRHH aún no dio el visto bueno")

    await db.execute(text("""
        UPDATE ordenes_trabajo SET
            liberada_por_planificador = true,
            fecha_liberacion = now(),
            estado = 'autorizada'
        WHERE id = :id
    """), {"id": ot_id})

    # Crear entradas en panol_entregas para los materiales de la OT
    mats_r = await db.execute(text("""
        SELECT codigo_sap, descripcion, cantidad, unidad
        FROM ot_materiales WHERE ot_id = :oid
    """), {"oid": ot_id})
    for m in mats_r.fetchall():
        await db.execute(text("""
            INSERT INTO panol_entregas
                (id, ot_id, tipo, codigo_sap, descripcion, cantidad, unidad,
                 entregado_por_id, entregado_por_nombre)
            VALUES
                (gen_random_uuid(), :oid, 'material', :cs, :desc, :cant, :un,
                 :uid, :unombre)
        """), {
            "oid":     ot_id,
            "cs":      m.codigo_sap,
            "desc":    m.descripcion,
            "cant":    m.cantidad,
            "un":      m.unidad,
            "uid":     current_user["id"],
            "unombre": current_user["nombre_completo"],
        })

    await db.commit()
    return {"ok": True}


@router.get("/planificador/ots/{ot_id}/detalle")
async def detalle_ot(
    ot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    ot_r = await db.execute(text("""
        SELECT o.*, e.nombre AS equipo_nombre, e.codigo_interno, e.qr_code,
               p.nombre AS plan_nombre, p.horas_hito
        FROM ordenes_trabajo o
        LEFT JOIN equipos e ON e.id = o.equipo_id
        LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        WHERE o.id = :id
    """), {"id": ot_id})
    ot = ot_r.fetchone()
    if not ot:
        raise HTTPException(404, "OT no encontrada")

    mats_r = await db.execute(text("""
        SELECT codigo_sap, descripcion, cantidad, unidad, tipo
        FROM ot_materiales WHERE ot_id = :oid ORDER BY descripcion
    """), {"oid": ot_id})
    mats = mats_r.fetchall()

    return {
        "id": str(ot.id), "numero": ot.numero, "tipo": ot.tipo,
        "urgente": ot.urgente, "estado": ot.estado,
        "descripcion": ot.descripcion,
        "equipo_nombre": ot.equipo_nombre,
        "codigo_interno": ot.codigo_interno,
        "qr_code": ot.qr_code,
        "plan_nombre": ot.plan_nombre or "—",
        "horas_hito": ot.horas_hito,
        "horometro_apertura": ot.horometro_apertura,
        "horometro_cierre": ot.horometro_cierre,
        "rrhh_aprobado": ot.rrhh_aprobado,
        "liberada_por_planificador": ot.liberada_por_planificador,
        "mecanico_nombre": ot.mecanico_nombre,
        "auxiliar_nombre": ot.auxiliar_nombre,
        "epp_items": ot.epp_items or [],
        "fecha_apertura": ot.fecha_apertura.strftime("%d/%m/%Y %H:%M") if ot.fecha_apertura else "—",
        "fecha_cierre": ot.fecha_cierre.strftime("%d/%m/%Y %H:%M") if ot.fecha_cierre else "—",
        "materiales": [{"codigo_sap": m.codigo_sap, "descripcion": m.descripcion,
                        "cantidad": m.cantidad, "unidad": m.unidad,
                        "tipo": m.tipo} for m in mats],
    }


# ─── SOLICITUDES CORRECTIVAS ──────────────────────────────────────────────────

@router.get("/planificador/solicitudes-correctivas")
async def listar_solicitudes(
    estado: str = "pendiente",
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    result = await db.execute(text("""
        SELECT s.id, s.descripcion, s.urgente, s.estado,
               s.solicitante_nombre, s.solicitante_rol,
               s.created_at, s.motivo_rechazo,
               e.nombre AS equipo_nombre, e.codigo_interno
        FROM solicitudes_correctivas s
        LEFT JOIN equipos e ON e.id = s.equipo_id
        WHERE s.estado = :estado
        ORDER BY s.urgente DESC, s.created_at ASC
    """), {"estado": estado})
    rows = result.fetchall()
    return [{
        "id": str(r.id), "descripcion": r.descripcion,
        "urgente": r.urgente, "estado": r.estado,
        "solicitante": r.solicitante_nombre, "rol": r.solicitante_rol,
        "equipo_nombre": r.equipo_nombre, "codigo_interno": r.codigo_interno,
        "fecha": r.created_at.strftime("%d/%m/%Y %H:%M") if r.created_at else "—",
        "motivo_rechazo": r.motivo_rechazo,
    } for r in rows]


@router.put("/planificador/solicitudes-correctivas/{sol_id}/rechazar")
async def rechazar_solicitud(
    sol_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    if not payload.get("motivo"):
        raise HTTPException(400, "El motivo de rechazo es obligatorio")
    await db.execute(text("""
        UPDATE solicitudes_correctivas SET
            estado = 'rechazada', motivo_rechazo = :motivo, updated_at = now()
        WHERE id = :id
    """), {"motivo": payload["motivo"], "id": sol_id})
    await db.commit()
    return {"ok": True}


# ─── CORRECCIÓN HORÓMETRO (PLANIFICADOR) ─────────────────────────────────────

@router.put("/planificador/horometros/{equipo_id}/corregir")
async def corregir_horometro_planificador(
    equipo_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("planificador"))
):
    """
    El planificador puede corregir cualquier fecha.
    Observación obligatoria.
    """
    if not payload.get("motivo"):
        raise HTTPException(400, "El motivo de corrección es obligatorio")

    result = await db.execute(text(
        "SELECT horometro_actual FROM equipos WHERE id = :id"
    ), {"id": equipo_id})
    equipo = result.fetchone()
    if not equipo:
        raise HTTPException(404, "Equipo no encontrado")

    await db.execute(text("""
        INSERT INTO horometros
            (id, equipo_id, usuario_id, lectura, lectura_anterior,
             es_correccion, lectura_original, motivo_correccion,
             corregido_por_rol, observaciones)
        VALUES
            (gen_random_uuid(), :eid, :uid, :lec, :ant,
             true, :orig, :motivo, 'planificador', :obs)
    """), {
        "eid": equipo_id, "uid": current_user["id"],
        "lec": payload["lectura"], "ant": equipo.horometro_actual,
        "orig": equipo.horometro_actual,
        "motivo": payload["motivo"], "obs": payload.get("observaciones"),
    })
    await db.execute(text(
        "UPDATE equipos SET horometro_actual = :lec WHERE id = :id"
    ), {"lec": payload["lectura"], "id": equipo_id})

    await db.execute(text("""
        INSERT INTO cambios_registro
            (id, tabla, registro_id, usuario_id, usuario_nombre,
             campo, valor_anterior, valor_nuevo, motivo)
        VALUES
            (gen_random_uuid(), 'equipos', :rid, :uid, :unombre,
             'horometro_actual', :ant, :nuevo, :motivo)
    """), {
        "rid": equipo_id, "uid": current_user["id"],
        "unombre": current_user["nombre_completo"],
        "ant": str(equipo.horometro_actual),
        "nuevo": str(payload["lectura"]),
        "motivo": payload["motivo"],
    })
    await db.commit()
    return {"ok": True}
