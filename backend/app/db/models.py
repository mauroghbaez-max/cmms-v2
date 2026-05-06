from sqlalchemy import Column, String, Boolean, Float, Integer, DateTime, Text, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base
import uuid

def gen_uuid():
    return str(uuid.uuid4())

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    username = Column(String(100), unique=True, nullable=False)
    nombre_completo = Column(String(200), nullable=False)
    email = Column(String(200))
    hashed_password = Column(String(300), nullable=False)
    rol = Column(String(50), nullable=False)
    area = Column(String(100))
    activo = Column(Boolean, default=True)
    puede_ver_trazabilidad = Column(Boolean, default=False)
    email_notificaciones = Column(String(200))
    observaciones = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class EmpresaConfig(Base):
    __tablename__ = "empresa_config"
    id = Column(Integer, primary_key=True, default=1)
    nombre = Column(String(200), default="Mi Empresa")
    logo_base64 = Column(Text)
    logo_mime = Column(String(50))
    cuit = Column(String(50))
    direccion = Column(String(300))
    telefono = Column(String(100))
    email = Column(String(200))
    web = Column(String(200))
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class Equipo(Base):
    __tablename__ = "equipos"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    nombre = Column(String(200), nullable=False)
    codigo_interno = Column(String(100), unique=True, nullable=False)
    codigo_sap = Column(String(100))
    ubicacion = Column(String(300))
    sector = Column(String(200))
    gps_lat = Column(Float)
    gps_lng = Column(Float)
    marca = Column(String(100))
    modelo = Column(String(100))
    anio = Column(Integer)
    horometro_inicial = Column(Float, default=0)
    horometro_actual = Column(Float, default=0)
    foto1_base64 = Column(Text)
    foto2_base64 = Column(Text)
    qr_code = Column(Text)
    activo = Column(Boolean, default=True)
    observaciones = Column(Text)
    relevador_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    planes = relationship("PlanMantenimiento", back_populates="equipo", cascade="all, delete-orphan")
    horometros = relationship("Horometro", back_populates="equipo")

class PlanMantenimiento(Base):
    __tablename__ = "planes_mantenimiento"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    equipo_id = Column(UUID(as_uuid=False), ForeignKey("equipos.id"), nullable=False)
    nombre = Column(String(200), nullable=False)
    horas_hito = Column(Float, nullable=False)
    horas_alerta = Column(Float, default=150)
    cantidad_personal = Column(Integer, default=1)
    observaciones = Column(Text)
    activo = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    equipo = relationship("Equipo", back_populates="planes")
    materiales = relationship("PlanMaterial", back_populates="plan", cascade="all, delete-orphan")
    ordenes = relationship("OrdenTrabajo", back_populates="plan")

class PlanMaterial(Base):
    __tablename__ = "plan_materiales"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    plan_id = Column(UUID(as_uuid=False), ForeignKey("planes_mantenimiento.id"), nullable=False)
    codigo_sap = Column(String(100))
    descripcion = Column(String(300), nullable=False)
    cantidad = Column(Float, default=1)
    unidad = Column(String(20), default="UN")
    observaciones = Column(Text)
    plan = relationship("PlanMantenimiento", back_populates="materiales")

class Horometro(Base):
    __tablename__ = "horometros"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    equipo_id = Column(UUID(as_uuid=False), ForeignKey("equipos.id"), nullable=False)
    usuario_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id"))
    lectura = Column(Float, nullable=False)
    lectura_anterior = Column(Float)
    fecha = Column(DateTime(timezone=True), server_default=func.now())
    es_correccion = Column(Boolean, default=False)
    lectura_original = Column(Float)
    motivo_correccion = Column(Text)
    observaciones = Column(Text)
    equipo = relationship("Equipo", back_populates="horometros")

class OrdenTrabajo(Base):
    __tablename__ = "ordenes_trabajo"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    numero = Column(String(20), unique=True, nullable=False)
    equipo_id = Column(UUID(as_uuid=False), ForeignKey("equipos.id"), nullable=False)
    plan_id = Column(UUID(as_uuid=False), ForeignKey("planes_mantenimiento.id"))
    planificador_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id"))
    estado = Column(String(50), default="borrador")
    descripcion = Column(Text)
    vehiculo_traslado = Column(String(200))
    horometro_apertura = Column(Float)
    horometro_cierre = Column(Float)
    fecha_apertura = Column(DateTime(timezone=True), server_default=func.now())
    fecha_liberacion = Column(DateTime(timezone=True))
    fecha_aprobacion_rrhh = Column(DateTime(timezone=True))
    fecha_cierre = Column(DateTime(timezone=True))
    mecanico_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id"))
    mecanico_nombre = Column(String(200))
    auxiliar_nombre = Column(String(200))
    cantidad_personal = Column(Integer)
    epp_detalle = Column(Text)
    epp_items = Column(JSON, default=list)
    documentos_rrhh = Column(JSON, default=list)
    checklist = Column(JSON, default=dict)
    rrhh_aprobado = Column(Boolean, default=False)
    panol_aprobado = Column(Boolean, default=False)
    liberada_por_planificador = Column(Boolean, default=False)
    es_parcial = Column(Boolean, default=False)
    observaciones = Column(Text)
    observaciones_cierre = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    equipo = relationship("Equipo")
    plan = relationship("PlanMantenimiento", back_populates="ordenes")
    materiales = relationship("OTMaterial", back_populates="ot", cascade="all, delete-orphan")
    entregas = relationship("PanolEntrega", back_populates="ot")
    auditoria = relationship("OTAuditoria", back_populates="ot")

class OTMaterial(Base):
    __tablename__ = "ot_materiales"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    ot_id = Column(UUID(as_uuid=False), ForeignKey("ordenes_trabajo.id"), nullable=False)
    codigo_sap = Column(String(100))
    descripcion = Column(String(300), nullable=False)
    cantidad = Column(Float, default=1)
    unidad = Column(String(20), default="UN")
    tipo = Column(String(50), default="material")
    agregado_por_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id"))
    agregado_por_nombre = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    ot = relationship("OrdenTrabajo", back_populates="materiales")

class PanolEntrega(Base):
    __tablename__ = "panol_entregas"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    ot_id = Column(UUID(as_uuid=False), ForeignKey("ordenes_trabajo.id"), nullable=False)
    tipo = Column(String(50), default="material")
    codigo_sap = Column(String(100))
    descripcion = Column(String(300), nullable=False)
    cantidad = Column(Float, default=1)
    unidad = Column(String(20), default="UN")
    entregado_por_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id"))
    entregado_por_nombre = Column(String(200))
    firma_operario = Column(Text)
    fecha_entrega = Column(DateTime(timezone=True), server_default=func.now())
    observaciones = Column(Text)
    ot = relationship("OrdenTrabajo", back_populates="entregas")

class OTAuditoria(Base):
    __tablename__ = "ot_auditoria"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    ot_id = Column(UUID(as_uuid=False), ForeignKey("ordenes_trabajo.id"), nullable=False)
    usuario_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id"))
    usuario_nombre = Column(String(200))
    accion = Column(String(100))
    detalle = Column(Text)
    datos_anteriores = Column(JSON)
    datos_nuevos = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    ot = relationship("OrdenTrabajo", back_populates="auditoria")

class LogAcceso(Base):
    __tablename__ = "log_accesos"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    usuario_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id"))
    usuario_nombre = Column(String(200))
    accion = Column(String(100))
    modulo = Column(String(100))
    ip = Column(String(50))
    dispositivo = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class CambioRegistro(Base):
    __tablename__ = "cambios_registro"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tabla = Column(String(100))
    registro_id = Column(String(100))
    usuario_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id"))
    usuario_nombre = Column(String(200))
    campo = Column(String(100))
    valor_anterior = Column(Text)
    valor_nuevo = Column(Text)
    motivo = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
