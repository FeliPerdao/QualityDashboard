import os
import secrets
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select, func, case, cast, Date, and_
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .database import Base, engine, get_db, SessionLocal
from .models import Isla, Defecto, Registro, registro_defectos, ahora
from .schemas import RegistroIn

load_dotenv()

BASE_DIR = Path(__file__).parent
ISLAS = ["Respaldo delantero", "Respaldo trasero", "Asiento delantero", "Asiento trasero"]

APP_USER = os.getenv("APP_USER")
APP_PASSWORD = os.getenv("APP_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY")
COOKIE_SECURE = os.getenv("COOKIE_SECURE") == "1"
if not (APP_USER and APP_PASSWORD and SECRET_KEY):
    raise RuntimeError("Faltan variables de entorno: APP_USER, APP_PASSWORD, SECRET_KEY")


def sincronizar_defectos(s: Session):
    archivo = BASE_DIR / "defectos.txt"
    if not archivo.exists():
        return
    nombres = [
        l.strip()
        for l in archivo.read_text(encoding="utf-8-sig").splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    existentes = {d.nombre: d for d in s.scalars(select(Defecto))}
    for orden, n in enumerate(nombres):
        d = existentes.get(n)
        if d:
            d.activo, d.orden = True, orden
        else:
            s.add(Defecto(nombre=n, orden=orden, activo=True))
    for n, d in existentes.items():
        if n not in nombres:
            d.activo = False
    s.commit()


Base.metadata.create_all(bind=engine)

with SessionLocal() as s:
    if not s.scalar(select(func.count(Isla.id))):
        s.add_all([Isla(nombre=n) for n in ISLAS])
        s.commit()
    sincronizar_defectos(s)

# Sin /docs ni /openapi.json públicos
app = FastAPI(title="Quality Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    https_only=COOKIE_SECURE,
    same_site="lax",
    max_age=60 * 60 * 12,  # la sesión dura 12 horas
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


def requiere_login(request: Request):
    if not request.session.get("auth"):
        raise HTTPException(401, "No autenticado")


api = APIRouter(dependencies=[Depends(requiere_login)])


# ---------- Login ----------
LOGIN_HTML = """<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ingresar · Quality Dashboard</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body class="bg-light d-flex align-items-center" style="min-height:100vh">
<div class="container" style="max-width:380px">
  <div class="card shadow-sm"><div class="card-body">
    <h5 class="mb-3">Quality Dashboard</h5>
    <input id="u" class="form-control mb-2" placeholder="Usuario" autocomplete="username">
    <input id="p" type="password" class="form-control mb-3" placeholder="Clave" autocomplete="current-password">
    <button id="b" class="btn btn-primary w-100">Ingresar</button>
    <div id="m" class="text-danger small mt-2"></div>
  </div></div>
</div>
<script>
const $ = (i) => document.getElementById(i);
async function entrar() {
  const r = await fetch("/login", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({usuario: $("u").value, clave: $("p").value}),
  });
  if (r.ok) location.href = "/";
  else $("m").textContent = (await r.json()).detail || "Error";
}
$("b").onclick = entrar;
$("p").addEventListener("keydown", (e) => { if (e.key === "Enter") entrar(); });
</script></body></html>"""


class LoginIn(BaseModel):
    usuario: str
    clave: str


@app.get("/login")
def pagina_login(request: Request):
    if request.session.get("auth"):
        return RedirectResponse("/")
    return HTMLResponse(LOGIN_HTML)


@app.post("/login")
def login(datos: LoginIn, request: Request):
    ok_u = secrets.compare_digest(datos.usuario.encode(), APP_USER.encode())
    ok_p = secrets.compare_digest(datos.clave.encode(), APP_PASSWORD.encode())
    if not (ok_u and ok_p):
        raise HTTPException(401, "Usuario o clave incorrectos")
    request.session["auth"] = True
    return {"ok": True}


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/")
def inicio(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login")
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Cálculos ----------
def calc_ippm(defectuosas, liberadas):
    return round(defectuosas / liberadas * 1_000_000, 1) if liberadas else None


def armar(id_, nombre, ok, defecto, retrab):
    return {
        "id": id_,
        "isla": nombre,
        "ok": ok,
        "con_defecto": defecto,
        "retrabajadas": retrab,
        "controladas": ok + defecto + retrab,
        "en_retrabajo": max(0, defecto - retrab),
        "ippm": calc_ippm(defecto, ok + retrab),
    }


def contar_acumulado(db: Session, isla_id: int):
    n_def = db.scalar(select(func.count(Registro.id)).where(
        Registro.isla_id == isla_id, Registro.con_defecto.is_(True)))
    n_ret = db.scalar(select(func.count(Registro.id)).where(
        Registro.isla_id == isla_id, Registro.retrabajada.is_(True)))
    return n_def, n_ret


# ---------- API protegida ----------
@api.get("/islas")
def listar_islas(db: Session = Depends(get_db)):
    islas = db.scalars(select(Isla).where(Isla.activa).order_by(Isla.id)).all()
    return [{"id": i.id, "nombre": i.nombre} for i in islas]


@api.get("/defectos")
def listar_defectos(db: Session = Depends(get_db)):
    sincronizar_defectos(db)
    ds = db.scalars(select(Defecto).where(Defecto.activo).order_by(Defecto.orden)).all()
    return [{"id": d.id, "nombre": d.nombre} for d in ds]


@api.post("/registros")
def crear_registro(datos: RegistroIn, db: Session = Depends(get_db)):
    if not db.get(Isla, datos.isla_id):
        raise HTTPException(404, "La isla no existe")
    if datos.retrabajada and datos.defecto_ids:
        raise HTTPException(400, "Una funda retrabajada liberada no lleva defectos")
    if datos.retrabajada:
        n_def, n_ret = contar_acumulado(db, datos.isla_id)
        if n_ret >= n_def:
            raise HTTPException(409, "No hay fundas en retrabajo en esta isla")
    ids = set(datos.defecto_ids)
    defectos = (
        db.scalars(select(Defecto).where(Defecto.id.in_(ids), Defecto.activo)).all()
        if ids
        else []
    )
    if len(defectos) != len(ids):
        raise HTTPException(400, "Algún defecto no existe o está desactivado")
    r = Registro(
        isla_id=datos.isla_id,
        con_defecto=bool(defectos),
        retrabajada=datos.retrabajada,
        defectos=defectos,
    )
    db.add(r)
    db.commit()
    return {"id": r.id, "con_defecto": r.con_defecto, "retrabajada": r.retrabajada}


@api.delete("/registros/{registro_id}")
def borrar_registro(registro_id: int, db: Session = Depends(get_db)):
    r = db.get(Registro, registro_id)
    if not r:
        raise HTTPException(404, "El registro no existe")
    if r.con_defecto:
        n_def, n_ret = contar_acumulado(db, r.isla_id)
        if n_def - 1 < n_ret:
            raise HTTPException(
                409, "No se puede deshacer: hay fundas retrabajadas que dependen de esa funda con defecto"
            )
    db.delete(r)
    db.commit()
    return {"borrado": registro_id}


@api.get("/ippm")
def ippm(desde: date | None = None, hasta: date | None = None, db: Session = Depends(get_db)):
    desde = desde or ahora().date()
    hasta = hasta or desde
    if desde > hasta:
        raise HTTPException(400, "Rango de fechas inválido")
    dia = cast(Registro.creado_en, Date)

    es_ok = case((and_(Registro.con_defecto.is_(False), Registro.retrabajada.is_(False)), 1), else_=0)
    es_def = case((Registro.con_defecto.is_(True), 1), else_=0)
    es_ret = case((Registro.retrabajada.is_(True), 1), else_=0)

    filas = db.execute(
        select(
            Isla.id,
            Isla.nombre,
            func.coalesce(func.sum(es_ok), 0),
            func.coalesce(func.sum(es_def), 0),
            func.coalesce(func.sum(es_ret), 0),
        )
        .outerjoin(Registro, and_(Registro.isla_id == Isla.id, dia.between(desde, hasta)))
        .where(Isla.activa)
        .group_by(Isla.id, Isla.nombre)
        .order_by(Isla.id)
    ).all()
    islas = [armar(i, n, int(o), int(d), int(r)) for i, n, o, d, r in filas]
    total = armar(
        0,
        "Total",
        sum(x["ok"] for x in islas),
        sum(x["con_defecto"] for x in islas),
        sum(x["retrabajadas"] for x in islas),
    )

    filas_p = db.execute(
        select(Registro.isla_id, Defecto.nombre, func.count(registro_defectos.c.registro_id))
        .select_from(registro_defectos)
        .join(Registro, Registro.id == registro_defectos.c.registro_id)
        .join(Defecto, Defecto.id == registro_defectos.c.defecto_id)
        .where(dia.between(desde, hasta))
        .group_by(Registro.isla_id, Defecto.nombre)
    ).all()
    for x in islas:
        x["defectos"] = [
            {"defecto": n, "cantidad": int(c)} for i, n, c in filas_p if i == x["id"]
        ]
    catalogo = db.scalars(select(Defecto.nombre).order_by(Defecto.orden, Defecto.id)).all()

    horas = list(range(5, 15))  # 5:00 a 14:59
    hora = func.extract("hour", Registro.creado_en)
    filas_h = db.execute(
        select(Registro.isla_id, hora, func.count(Registro.id))
        .where(dia.between(desde, hasta), Registro.con_defecto.is_(False))
        .group_by(Registro.isla_id, hora)
    ).all()
    conteo = {(i, int(h)): int(c) for i, h, c in filas_h}
    por_hora = {
        "horas": [f"{h}:00" for h in horas],
        "series": [
            {"isla": x["isla"], "datos": [conteo.get((x["id"], h), 0) for h in horas]}
            for x in islas
        ],
    }

    return {
        "desde": desde,
        "hasta": hasta,
        "total": total,
        "islas": islas,
        "catalogo_defectos": list(catalogo),
        "por_hora": por_hora,
    }


app.include_router(api)