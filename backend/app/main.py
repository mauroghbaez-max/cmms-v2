from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.db.session import engine, Base
from app.db.models import *
from app.core.security import hash_password
from sqlalchemy import text
import os

app = FastAPI(title="MANTEN. v2.0", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    print("Iniciando MANTEN. v2.0...", flush=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Tablas OK", flush=True)
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            resultado = await db.execute(text("SELECT id FROM usuarios WHERE username = 'admin'"))
            if not resultado.fetchone():
                await db.execute(text("""
                    INSERT INTO usuarios (id, username, nombre_completo, email, hashed_password, rol, activo, puede_ver_trazabilidad)
                    VALUES (gen_random_uuid(), 'admin', 'Administrador', 'admin@manten.com', :pwd, 'admin', true, true)
                """), {"pwd": hash_password("admin1234")})
                await db.commit()
                print("Admin creado — user: admin / pass: admin1234", flush=True)
        except Exception as e:
            print(f"Error startup: {e}", flush=True)

@app.get("/health")
async def health():
    return {"status": "ok"}

from app.api.v1.endpoints import routes
app.include_router(routes.router, prefix="/api/v1")
from app.api.v1.endpoints import planificador_routes
app.include_router(planificador_routes.router, prefix="/api/v1")
from app.api.v1.endpoints import rrhh_routes
app.include_router(rrhh_routes.router, prefix="/api/v1")
from app.api.v1.endpoints import panol_routes
app.include_router(panol_routes.router, prefix="/api/v1")
from app.api.v1.endpoints import operador_routes
app.include_router(operador_routes.router, prefix="/api/v1")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app.mount("/app", StaticFiles(directory=os.path.join(BASE_DIR, "frontend"), html=True), name="frontend")