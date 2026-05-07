from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db.session import get_db
from app.api.v1.endpoints.routes import get_current_user, require_rol
import uuid, base64

router = APIRouter()

# ─── LISTAR OTs ASIGNADAS A RRHH ─────────────────────────────────────────────

@router.get("/rrhh/ots")
async def rrhh_listar_ots(
    estado: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("rrhh"))
):
    where = "o.estado NOT IN ('completada','cancelada')"
    params = {}
    if estado:
        where = "o.estado = :estado"
        params["estado"] = estado

    result = await db.execute(text(f"""
        SELECT o.id, o.numero, o.tipo, o.urgente, o.estado,
               o.descripcion, o.rrhh_aprobado, o.fecha_apertura,
               o.mecanico_nombre, o.auxiliar_nombre, o.cantidad_personal,
               o.epp_items, o.documentos_rrhh, o.observaciones,
               o.vehiculo_traslado,
               e.nombre AS equipo_nombre, e.codigo_interno,
               p.nombre AS plan_nombre, p.horas_hito, p.cantidad_personal AS plan_personal
        FROM ordenes_trabajo o
        LEFT JOIN equipos e ON e.id = o.equipo_id
        LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        WHERE {where}
        ORDER BY o.urgente DESC, o.fecha_apertura ASC
    """), params)
    rows = result.fetchall()
    return [{
        "id": str(r.id), "numero": r.numero, "tipo": r.tipo,
        "urgente": r.urgente, "estado": r.estado,
        "descripcion": r.descripcion, "rrhh_aprobado": r.rrhh_aprobado,
        "equipo_nombre": r.equipo_nombre, "codigo_interno": r.codigo_interno,
        "plan_nombre": r.plan_nombre or "—", "horas_hito": r.horas_hito,
        "plan_personal": r.plan_personal or 1,
        "mecanico_nombre": r.mecanico_nombre,
        "auxiliar_nombre": r.auxiliar_nombre,
        "cantidad_personal": r.cantidad_personal,
        "epp_items": r.epp_items or [],
        "documentos_rrhh": r.documentos_rrhh or [],
        "observaciones": r.observaciones,
        "vehiculo_traslado": r.vehiculo_traslado,
        "fecha_apertura": r.fecha_apertura.strftime("%d/%m/%Y %H:%M") if r.fecha_apertura else "—",
    } for r in rows]


@router.get("/rrhh/ots/{ot_id}")
async def rrhh_detalle_ot(
    ot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("rrhh"))
):
    result = await db.execute(text("""
        SELECT o.*, e.nombre AS equipo_nombre, e.codigo_interno, e.ubicacion,
               p.nombre AS plan_nombre, p.horas_hito, p.cantidad_personal AS plan_personal
        FROM ordenes_trabajo o
        LEFT JOIN equipos e ON e.id = o.equipo_id
        LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        WHERE o.id = :id
    """), {"id": ot_id})
    ot = result.fetchone()
    if not ot:
        raise HTTPException(404, "OT no encontrada")

    mats = await db.execute(text("""
        SELECT descripcion, cantidad, unidad, tipo
        FROM ot_materiales WHERE ot_id = :oid ORDER BY descripcion
    """), {"oid": ot_id})

    return {
        "id": str(ot.id), "numero": ot.numero, "tipo": ot.tipo,
        "urgente": ot.urgente, "estado": ot.estado,
        "descripcion": ot.descripcion,
        "equipo_nombre": ot.equipo_nombre,
        "codigo_interno": ot.codigo_interno,
        "ubicacion": ot.ubicacion,
        "plan_nombre": ot.plan_nombre or "—",
        "horas_hito": ot.horas_hito,
        "plan_personal": ot.plan_personal or 1,
        "vehiculo_traslado": ot.vehiculo_traslado,
        "horometro_apertura": ot.horometro_apertura,
        "rrhh_aprobado": ot.rrhh_aprobado,
        "fecha_aprobacion_rrhh": ot.fecha_aprobacion_rrhh.strftime("%d/%m/%Y %H:%M") if ot.fecha_aprobacion_rrhh else None,
        "mecanico_nombre": ot.mecanico_nombre,
        "auxiliar_nombre": ot.auxiliar_nombre,
        "cantidad_personal": ot.cantidad_personal,
        "epp_items": ot.epp_items or [],
        "documentos_rrhh": ot.documentos_rrhh or [],
        "observaciones": ot.observaciones,
        "fecha_apertura": ot.fecha_apertura.strftime("%d/%m/%Y %H:%M") if ot.fecha_apertura else "—",
        "materiales": [{"descripcion": m.descripcion, "cantidad": m.cantidad,
                        "unidad": m.unidad, "tipo": m.tipo} for m in mats.fetchall()],
    }


# ─── CARGAR DATOS RRHH ────────────────────────────────────────────────────────

@router.put("/rrhh/ots/{ot_id}/cargar")
async def rrhh_cargar_datos(
    ot_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("rrhh"))
):
    """
    RRHH carga personal, EPP y documentación.
    No da el visto bueno todavía — solo guarda los datos.
    """
    await db.execute(text("""
        UPDATE ordenes_trabajo SET
            mecanico_nombre    = :mecanico,
            auxiliar_nombre    = :auxiliar,
            cantidad_personal  = :cp,
            epp_items          = :epp,
            documentos_rrhh    = :docs,
            observaciones      = :obs
        WHERE id = :id
    """), {
        "mecanico":  payload.get("mecanico_nombre"),
        "auxiliar":  payload.get("auxiliar_nombre"),
        "cp":        payload.get("cantidad_personal"),
        "epp":       payload.get("epp_items", []),
        "docs":      payload.get("documentos_rrhh", []),
        "obs":       payload.get("observaciones"),
        "id":        ot_id,
    })
    await db.commit()
    return {"ok": True}


# ─── VISTO BUENO RRHH ─────────────────────────────────────────────────────────

@router.post("/rrhh/ots/{ot_id}/aprobar")
async def rrhh_aprobar(
    ot_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("rrhh"))
):
    """
    Visto bueno de RRHH. Dispara 2 acciones:
    1) Marca rrhh_aprobado = true → habilita al planificador a imprimir
    2) Crea entradas en panol_entregas para los EPP → Pañol recibe la orden
    """
    # Validar que tenga personal cargado
    ot_r = await db.execute(text("""
        SELECT id, mecanico_nombre, epp_items, rrhh_aprobado, numero, equipo_id
        FROM ordenes_trabajo WHERE id = :id
    """), {"id": ot_id})
    ot = ot_r.fetchone()
    if not ot:
        raise HTTPException(404, "OT no encontrada")
    if ot.rrhh_aprobado:
        raise HTTPException(400, "RRHH ya aprobó esta OT")
    if not ot.mecanico_nombre:
        raise HTTPException(400, "Debe cargar al menos el mecánico antes de aprobar")

    # 1) Marcar aprobado
    await db.execute(text("""
        UPDATE ordenes_trabajo SET
            rrhh_aprobado = true,
            fecha_aprobacion_rrhh = now(),
            estado = 'autorizada'
        WHERE id = :id
    """), {"id": ot_id})

    # 2) Crear orden de entrega de EPP al Pañol
    epp_items = ot.epp_items or []
    if isinstance(epp_items, str):
        import json
        epp_items = json.loads(epp_items)

    for epp in epp_items:
        await db.execute(text("""
            INSERT INTO panol_entregas
                (id, ot_id, tipo, descripcion, cantidad, unidad,
                 entregado_por_id, entregado_por_nombre)
            VALUES
                (gen_random_uuid(), :oid, 'epp', :desc, :cant, :un,
                 :uid, :unombre)
        """), {
            "oid":     ot_id,
            "desc":    epp.get("descripcion", "EPP"),
            "cant":    epp.get("cantidad", 1),
            "un":      epp.get("unidad", "UN"),
            "uid":     current_user["id"],
            "unombre": current_user["nombre_completo"],
        })

    # Registrar en auditoría
    await db.execute(text("""
        INSERT INTO ot_auditoria
            (id, ot_id, usuario_id, usuario_nombre, accion, detalle)
        VALUES
            (gen_random_uuid(), :oid, :uid, :unombre, 'rrhh_aprobado',
             'RRHH dio visto bueno. EPP liberados al Pañol.')
    """), {
        "oid":     ot_id,
        "uid":     current_user["id"],
        "unombre": current_user["nombre_completo"],
    })

    await db.commit()
    return {"ok": True}
