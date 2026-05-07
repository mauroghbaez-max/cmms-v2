from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db.session import get_db
from app.api.v1.endpoints.routes import get_current_user, require_rol
import uuid

router = APIRouter()

# ─── BUSCAR OT POR QR / CÓDIGO EQUIPO ────────────────────────────────────────

@router.get("/operador/ot-por-equipo/{codigo_interno}")
async def ot_por_equipo(
    codigo_interno: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("operario"))
):
    """
    El operador escanea el QR del equipo y el sistema busca
    la OT activa (autorizada) para ese equipo.
    """
    result = await db.execute(text("""
        SELECT o.id, o.numero, o.tipo, o.urgente, o.estado,
               o.descripcion, o.horometro_apertura,
               o.mecanico_nombre, o.vehiculo_traslado,
               o.fecha_apertura, o.liberada_por_planificador,
               e.nombre AS equipo_nombre, e.codigo_interno, e.horometro_actual,
               p.nombre AS plan_nombre, p.horas_hito
        FROM ordenes_trabajo o
        JOIN equipos e ON e.id = o.equipo_id
        LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        WHERE e.codigo_interno = :ci
          AND o.estado IN ('autorizada', 'en_ejecucion')
          AND o.liberada_por_planificador = true
        ORDER BY o.fecha_apertura DESC
        LIMIT 1
    """), {"ci": codigo_interno})
    ot = result.fetchone()
    if not ot:
        raise HTTPException(404, "No hay OT activa para este equipo")

    # Traer materiales del checklist
    mats = await db.execute(text("""
        SELECT id, descripcion, cantidad, unidad, tipo
        FROM ot_materiales WHERE ot_id = :oid ORDER BY descripcion
    """), {"oid": str(ot.id)})

    return {
        "id": str(ot.id), "numero": ot.numero,
        "tipo": ot.tipo, "urgente": ot.urgente, "estado": ot.estado,
        "descripcion": ot.descripcion,
        "equipo_nombre": ot.equipo_nombre,
        "codigo_interno": ot.codigo_interno,
        "horometro_actual": ot.horometro_actual,
        "horometro_apertura": ot.horometro_apertura,
        "plan_nombre": ot.plan_nombre or "—",
        "horas_hito": ot.horas_hito,
        "mecanico_nombre": ot.mecanico_nombre,
        "vehiculo_traslado": ot.vehiculo_traslado,
        "fecha_apertura": ot.fecha_apertura.strftime("%d/%m/%Y %H:%M") if ot.fecha_apertura else "—",
        "materiales": [{"id": str(m.id), "descripcion": m.descripcion,
                        "cantidad": m.cantidad, "unidad": m.unidad,
                        "tipo": m.tipo} for m in mats.fetchall()],
    }


@router.get("/operador/ot/{ot_id}")
async def operador_detalle_ot(
    ot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("operario"))
):
    result = await db.execute(text("""
        SELECT o.id, o.numero, o.tipo, o.urgente, o.estado,
               o.descripcion, o.horometro_apertura, o.checklist,
               o.mecanico_nombre, o.vehiculo_traslado, o.liberada_por_planificador,
               o.fecha_apertura,
               e.nombre AS equipo_nombre, e.codigo_interno, e.horometro_actual,
               p.nombre AS plan_nombre, p.horas_hito
        FROM ordenes_trabajo o
        JOIN equipos e ON e.id = o.equipo_id
        LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        WHERE o.id = :id
    """), {"id": ot_id})
    ot = result.fetchone()
    if not ot:
        raise HTTPException(404, "OT no encontrada")
    if not ot.liberada_por_planificador:
        raise HTTPException(400, "OT no liberada por el planificador todavía")

    mats = await db.execute(text("""
        SELECT id, descripcion, cantidad, unidad, tipo
        FROM ot_materiales WHERE ot_id = :oid ORDER BY descripcion
    """), {"oid": ot_id})

    import json
    checklist = ot.checklist or {}
    if isinstance(checklist, str):
        checklist = json.loads(checklist)

    return {
        "id": str(ot.id), "numero": ot.numero,
        "tipo": ot.tipo, "urgente": ot.urgente, "estado": ot.estado,
        "descripcion": ot.descripcion,
        "equipo_nombre": ot.equipo_nombre,
        "codigo_interno": ot.codigo_interno,
        "horometro_actual": ot.horometro_actual,
        "horometro_apertura": ot.horometro_apertura,
        "plan_nombre": ot.plan_nombre or "—",
        "horas_hito": ot.horas_hito,
        "mecanico_nombre": ot.mecanico_nombre,
        "fecha_apertura": ot.fecha_apertura.strftime("%d/%m/%Y %H:%M") if ot.fecha_apertura else "—",
        "checklist": checklist,
        "materiales": [{"id": str(m.id), "descripcion": m.descripcion,
                        "cantidad": m.cantidad, "unidad": m.unidad,
                        "tipo": m.tipo} for m in mats.fetchall()],
    }


# ─── INICIAR EJECUCIÓN ────────────────────────────────────────────────────────

@router.post("/operador/ot/{ot_id}/iniciar")
async def operador_iniciar(
    ot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("operario"))
):
    """Marca la OT como en ejecución cuando el operador llega al lugar."""
    await db.execute(text("""
        UPDATE ordenes_trabajo SET
            estado = 'en_ejecucion',
            mecanico_id = :uid
        WHERE id = :id AND estado = 'autorizada'
    """), {"uid": current_user["id"], "id": ot_id})
    await db.commit()
    return {"ok": True}


# ─── ACTUALIZAR CHECKLIST ─────────────────────────────────────────────────────

@router.put("/operador/ot/{ot_id}/checklist")
async def operador_checklist(
    ot_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("operario"))
):
    """
    El operador va tildando los materiales a medida que los cambia.
    checklist es un dict {material_id: true/false}
    """
    import json
    await db.execute(text("""
        UPDATE ordenes_trabajo SET checklist = :cl WHERE id = :id
    """), {"cl": json.dumps(payload.get("checklist", {})), "id": ot_id})
    await db.commit()
    return {"ok": True}


# ─── CERRAR OT ────────────────────────────────────────────────────────────────

@router.post("/operador/ot/{ot_id}/cerrar")
async def operador_cerrar_ot(
    ot_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("operario"))
):
    """
    El operador cierra la OT. Dispara 2 acciones:
    1) Cierre de OT → planificador puede imprimir el documento final
    2) Pedido preventivo → pañol recibe materiales del próximo servicio

    Requiere: horometro_cierre (obligatorio)
    """
    horometro = payload.get("horometro_cierre")
    if not horometro:
        raise HTTPException(400, "El horómetro de cierre es obligatorio")

    import json

    # Obtener datos de la OT
    ot_r = await db.execute(text("""
        SELECT o.id, o.equipo_id, o.plan_id, o.numero, o.checklist
        FROM ordenes_trabajo o
        WHERE o.id = :id AND o.estado IN ('autorizada','en_ejecucion')
    """), {"id": ot_id})
    ot = ot_r.fetchone()
    if not ot:
        raise HTTPException(404, "OT no encontrada o ya cerrada")

    checklist = payload.get("checklist", ot.checklist or {})
    if isinstance(checklist, str):
        checklist = json.loads(checklist)

    # 1) Cerrar la OT
    await db.execute(text("""
        UPDATE ordenes_trabajo SET
            estado = 'completada',
            horometro_cierre = :hc,
            fecha_cierre = now(),
            checklist = :cl,
            observaciones_cierre = :obs
        WHERE id = :id
    """), {
        "hc":  horometro,
        "cl":  json.dumps(checklist),
        "obs": payload.get("observaciones_cierre"),
        "id":  ot_id,
    })

    # Actualizar horómetro del equipo
    await db.execute(text("""
        UPDATE equipos SET horometro_actual = :hc WHERE id = :eid
    """), {"hc": horometro, "eid": str(ot.equipo_id)})

    # Registrar horómetro en historial
    await db.execute(text("""
        INSERT INTO horometros
            (id, equipo_id, usuario_id, lectura, es_correccion, observaciones)
        VALUES
            (gen_random_uuid(), :eid, :uid, :lec, false, 'Cierre de OT')
    """), {"eid": str(ot.equipo_id), "uid": current_user["id"], "lec": horometro})

    # 2) Crear pedido preventivo al pañol
    # Buscar el próximo plan para este equipo
    if ot.plan_id:
        plan_r = await db.execute(text("""
            SELECT horas_hito FROM planes_mantenimiento WHERE id = :pid
        """), {"pid": str(ot.plan_id)})
        plan_actual = plan_r.fetchone()

        if plan_actual:
            proximo_plan_r = await db.execute(text("""
                SELECT id FROM planes_mantenimiento
                WHERE equipo_id = :eid
                  AND horas_hito > :hh
                  AND eliminado = false
                  AND activo = true
                ORDER BY horas_hito ASC
                LIMIT 1
            """), {"eid": str(ot.equipo_id), "hh": plan_actual.horas_hito})
            proximo_plan = proximo_plan_r.fetchone()

            if proximo_plan:
                # Agregar materiales del próximo plan como pedido preventivo al pañol
                mats_r = await db.execute(text("""
                    SELECT codigo_sap, descripcion, cantidad, unidad
                    FROM plan_materiales WHERE plan_id = :pid
                """), {"pid": str(proximo_plan.id)})

                for m in mats_r.fetchall():
                    await db.execute(text("""
                        INSERT INTO panol_entregas
                            (id, ot_id, tipo, codigo_sap, descripcion, cantidad, unidad,
                             observaciones)
                        VALUES
                            (gen_random_uuid(), :oid, 'preventivo', :cs, :desc, :cant, :un,
                             'Pedido preventivo próximo servicio')
                    """), {
                        "oid":  ot_id,
                        "cs":   m.codigo_sap,
                        "desc": m.descripcion,
                        "cant": m.cantidad,
                        "un":   m.unidad,
                    })

    # Auditoría
    await db.execute(text("""
        INSERT INTO ot_auditoria
            (id, ot_id, usuario_id, usuario_nombre, accion, detalle)
        VALUES
            (gen_random_uuid(), :oid, :uid, :unombre, 'cierre_operador',
             :detalle)
    """), {
        "oid":     ot_id,
        "uid":     current_user["id"],
        "unombre": current_user["nombre_completo"],
        "detalle": f"Operador cerró OT. Horómetro cierre: {horometro} hs",
    })

    await db.commit()
    return {"ok": True, "numero": ot.numero}


# ─── SOLICITAR OT CORRECTIVA ──────────────────────────────────────────────────

@router.post("/operador/solicitud-correctiva")
async def operador_solicitar_correctiva(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("operario"))
):
    """
    El operador reporta una falla o rotura.
    Crea una solicitud correctiva que le llega al planificador.
    """
    if not payload.get("equipo_id"):
        raise HTTPException(400, "El equipo es obligatorio")
    if not payload.get("descripcion"):
        raise HTTPException(400, "La descripción es obligatoria")

    sol_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO solicitudes_correctivas
            (id, equipo_id, solicitante_id, solicitante_nombre,
             solicitante_rol, descripcion, urgente, estado)
        VALUES
            (:id, :eid, :uid, :unombre, 'operario', :desc, :urgente, 'pendiente')
    """), {
        "id":      sol_id,
        "eid":     payload["equipo_id"],
        "uid":     current_user["id"],
        "unombre": current_user["nombre_completo"],
        "desc":    payload["descripcion"],
        "urgente": payload.get("urgente", False),
    })
    await db.commit()
    return {"ok": True, "id": sol_id}


# ─── LISTAR EQUIPOS PARA EL OPERADOR ─────────────────────────────────────────

@router.get("/operador/equipos")
async def operador_equipos(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("operario"))
):
    result = await db.execute(text("""
        SELECT id, nombre, codigo_interno, horometro_actual
        FROM equipos WHERE activo = true ORDER BY nombre
    """))
    rows = result.fetchall()
    return [{"id": str(r.id), "nombre": r.nombre,
             "codigo_interno": r.codigo_interno,
             "horometro_actual": r.horometro_actual} for r in rows]
