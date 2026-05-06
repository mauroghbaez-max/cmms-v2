import asyncio
import sys
sys.path.insert(0, "/app")
from app.db.session import engine, Base
from app.db.models import *
from app.core.security import hash_password
from sqlalchemy import text

async def init():
    print("Creando tablas...", flush=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Tablas creadas OK", flush=True)

    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        resultado = await db.execute(text("SELECT id FROM usuarios WHERE username = 'admin'"))
        existe = resultado.fetchone()
        if not existe:
            await db.execute(text("""
                INSERT INTO usuarios (id, username, nombre_completo, email, hashed_password, rol, activo, puede_ver_trazabilidad)
                VALUES (gen_random_uuid(), 'admin', 'Administrador', 'admin@manten.com', :pwd, 'admin', true, true)
            """), {"pwd": hash_password("admin1234")})
            await db.commit()
            print("Usuario admin creado — password: admin1234", flush=True)
        else:
            print("Usuario admin ya existe", flush=True)

if __name__ == "__main__":
    asyncio.run(init())
