from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db.session import get_db
from app.api.v1.endpoints.routes import get_current_user, require_rol
import uuid

router = APIRouter()

# ─── LISTAR ÓRDENES PENDIENTES ────────────────────────────────────────────────

@router.get("/panol/ordenes")
async def panol_ordenes(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("panol"))
):
    """
    El pañol ve dos tipos de órdenes pendientes:
    1) Órdenes de EPP (cuando RRHH da visto bueno)
    2) Órdenes de materiales (cuando el planificador imprime la OT)
    """
    result = await db.execute(text("""
        SELECT
            pe.id, pe.ot_id, pe.tipo, pe.descripcion, pe.cantidad, pe.unidad,
            pe.fecha_entrega, pe.firma_operario, pe.observaciones,
            o.numero AS ot_numero, o.urgente, o.estado AS ot_estado,
            e.nombre AS equipo_nombre, e.codigo_interno
        FROM panol_entregas pe
        JOIN ordenes_trabajo o ON o.id = pe.ot_id
        JOIN equipos e ON e.id = o.equipo_id
        WHERE pe.firma_operario IS NULL
          AND o.estado NOT IN ('completada','cancelada')
        ORDER BY o.urgente DESC, pe.fecha_entrega ASC
    """))
    rows = result.fetchall()

    # Agrupar por OT
    ots = {}
    for r in rows:
        ot_id = str(r.ot_id)
        if ot_id not in ots:
            ots[ot_id] = {
                "ot_id": ot_id,
                "ot_numero": r.ot_numero,
                "urgente": r.urgente,
                "ot_estado": r.ot_estado,
                "equipo_nombre": r.equipo_nombre,
                "codigo_interno": r.codigo_interno,
                "items_epp": [],
                "items_material": [],
            }
        item = {
            "id": str(r.id),
            "descripcion": r.descripcion,
            "cantidad": r.cantidad,
            "unidad": r.unidad,
        }
        if r.tipo == "epp":
            ots[ot_id]["items_epp"].append(item)
        else:
            ots[ot_id]["items_material"].append(item)

    return list(ots.values())


@router.get("/panol/pedidos-preventivos")
async def panol_pedidos_preventivos(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("panol"))
):
    """
    Pedidos preventivos: materiales del próximo servicio
    generados al cerrar una OT por el operador.
    """
    # FIX: DISTINCT ON evita duplicados cuando hay múltiples OTs completadas
    # para el mismo equipo. Se queda con la más reciente por equipo.
    result = await db.execute(text("""
        SELECT
            o.id, o.numero, o.fecha_cierre,
            e.nombre AS equipo_nombre, e.codigo_interno,
            p_sig.nombre AS proximo_plan, p_sig.horas_hito AS proximas_horas,
            pm.codigo_sap, pm.descripcion, pm.cantidad, pm.unidad
        FROM (
            SELECT DISTINCT ON (equipo_id) *
            FROM ordenes_trabajo
            WHERE estado = 'completada'
              AND fecha_cierre >= NOW() - INTERVAL '7 days'
            ORDER BY equipo_id, fecha_cierre DESC
        ) o
        JOIN equipos e ON e.id = o.equipo_id
        JOIN planes_mantenimiento p_sig ON p_sig.equipo_id = e.id
            AND p_sig.horas_hito > COALESCE(o.horometro_cierre, 0)
            AND p_sig.eliminado = false
        JOIN plan_materiales pm ON pm.plan_id = p_sig.id
        ORDER BY o.fecha_cierre DESC, e.nombre, p_sig.horas_hito
    """))
    rows = result.fetchall()

    ots = {}
    for r in rows:
        key = str(r.id) + '_' + str(r.proximas_horas)
        if key not in ots:
            ots[key] = {
                "ot_id": str(r.id),
                "ot_numero": r.numero,
                "fecha_cierre": r.fecha_cierre.strftime("%d/%m/%Y %H:%M") if r.fecha_cierre else "—",
                "equipo_nombre": r.equipo_nombre,
                "codigo_interno": r.codigo_interno,
                "proximo_plan": r.proximo_plan,
                "proximas_horas": r.proximas_horas,
                "materiales": [],
            }
        ots[key]["materiales"].append({
            "codigo_sap": r.codigo_sap,
            "descripcion": r.descripcion,
            "cantidad": r.cantidad,
            "unidad": r.unidad,
        })

    return list(ots.values())


# ─── CONFIRMAR ENTREGA ────────────────────────────────────────────────────────

@router.post("/panol/entregas/{ot_id}/confirmar")
async def panol_confirmar_entrega(
    ot_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("panol"))
):
    """
    El pañol confirma la entrega de EPP y/o materiales al operador.
    Registra la firma del operador (base64 o texto).
    tipo: 'epp' | 'material' | 'ambos'
    """
    tipo = payload.get("tipo", "ambos")
    firma = payload.get("firma_operario", "Entregado")

    where_tipo = ""
    if tipo == "epp":
        where_tipo = "AND tipo = 'epp'"
    elif tipo == "material":
        where_tipo = "AND tipo != 'epp'"

    await db.execute(text(f"""
        UPDATE panol_entregas SET
            firma_operario = :firma,
            entregado_por_id = :uid,
            entregado_por_nombre = :unombre,
            observaciones = :obs
        WHERE ot_id = :oid
          AND firma_operario IS NULL
          {where_tipo}
    """), {
        "firma":   firma,
        "uid":     current_user["id"],
        "unombre": current_user["nombre_completo"],
        "obs":     payload.get("observaciones"),
        "oid":     ot_id,
    })

    await db.execute(text("""
        INSERT INTO ot_auditoria
            (id, ot_id, usuario_id, usuario_nombre, accion, detalle)
        VALUES
            (gen_random_uuid(), :oid, :uid, :unombre, 'panol_entrega',
             :detalle)
    """), {
        "oid":     ot_id,
        "uid":     current_user["id"],
        "unombre": current_user["nombre_completo"],
        "detalle": f"Pañol confirmó entrega de {tipo}",
    })

    await db.commit()
    return {"ok": True}


# ─── DETALLE DE OT PARA PAÑOL ─────────────────────────────────────────────────

@router.get("/panol/ots/{ot_id}")
async def panol_detalle_ot(
    ot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_rol("panol"))
):
    ot_r = await db.execute(text("""
        SELECT o.numero, o.tipo, o.urgente, o.estado,
               o.mecanico_nombre, o.auxiliar_nombre, o.cantidad_personal,
               o.fecha_apertura, o.vehiculo_traslado,
               e.nombre AS equipo_nombre, e.codigo_interno, e.qr_code,
               p.nombre AS plan_nombre
        FROM ordenes_trabajo o
        LEFT JOIN equipos e ON e.id = o.equipo_id
        LEFT JOIN planes_mantenimiento p ON p.id = o.plan_id
        WHERE o.id = :id
    """), {"id": ot_id})
    ot = ot_r.fetchone()
    if not ot:
        raise HTTPException(404, "OT no encontrada")

    entregas_r = await db.execute(text("""
        SELECT tipo, descripcion, cantidad, unidad, firma_operario, fecha_entrega
        FROM panol_entregas WHERE ot_id = :oid ORDER BY tipo, descripcion
    """), {"oid": ot_id})
    entregas = entregas_r.fetchall()

    return {
        "numero": ot.numero, "tipo": ot.tipo, "urgente": ot.urgente,
        "estado": ot.estado, "plan_nombre": ot.plan_nombre or "—",
        "equipo_nombre": ot.equipo_nombre, "codigo_interno": ot.codigo_interno,
        "qr_code": ot.qr_code,
        "mecanico_nombre": ot.mecanico_nombre, "auxiliar_nombre": ot.auxiliar_nombre,
        "cantidad_personal": ot.cantidad_personal,
        "vehiculo_traslado": ot.vehiculo_traslado,
        "fecha_apertura": ot.fecha_apertura.strftime("%d/%m/%Y %H:%M") if ot.fecha_apertura else "—",
        "entregas": [{
            "tipo": e.tipo, "descripcion": e.descripcion,
            "cantidad": e.cantidad, "unidad": e.unidad,
            "entregado": e.firma_operario is not None,
            "fecha_entrega": e.fecha_entrega.strftime("%d/%m/%Y %H:%M") if e.fecha_entrega else "—",
        } for e in entregas],
    }