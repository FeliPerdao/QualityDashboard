import csv
import io
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import JSON, DateTime, Integer, String, create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .models import ahora

load_dotenv()
BASE_DIR = Path(__file__).parent

PTS_ACIERTO = 10
PTS_ERROR = 5

# Orden en que se muestran las opciones (mezclado una sola vez)
OPCIONES = [
    "Pieza picada",
    "Dobladillo mal realizado",
    "Defecto de ISOFIX",
    "Bidín incorrecto",
    "Largo de puntada más chico",
    "TNT invertido",
    "Defecto en el codon",
    "Pespunte desalineado",
    "Bagueta invertida o incorrecta",
    "Hilo sueldo sin cortar",
    "Falta de bagueta",
    "Toma de costura desalineada",
    "Bidin invertido o incorrecto",
    "Defecto en la etiqueta",
    "Tejido/paño incorrecto",
    "Piquetes desplazados",
    "Defecto en el cierre",
]

# Respuestas correctas: NO se envían nunca al navegador
FUNDAS = {
    "1": {
        "titulo": "Funda 1 - Respaldo Trasero X6S",
        "correctos": [
            "Bagueta invertida o incorrecta",
            "TNT invertido",
            "Defecto de ISOFIX",
            "Piquetes desplazados",
            "Toma de costura desalineada",
            "Defecto en la etiqueta",
        ],
    },
    "2": {
        "titulo": "Funda 2 - Asiento Trasero P21",
        "correctos": [
            "Defecto de ISOFIX",
            "Bidín incorrecto",
            "Falta de bagueta",
            "Defecto en el cierre",
            "Defecto en el codon",
            "Bagueta invertida o incorrecta",
        ],
    },
    "3": {
        "titulo": "Funda 3 - Respaldo Delantero KP1",
        "correctos": [
            "Bagueta invertida o incorrecta",
            "Tejido/paño incorrecto",
            "Pieza picada",
            "Hilo sueldo sin cortar",
            "Bidin invertido o incorrecto",
        ],
    },
}
MAXIMO = sum(len(f["correctos"]) for f in FUNDAS.values()) * PTS_ACIERTO


# ---------- Base de datos SEPARADA ----------
def _url(u: str) -> str:
    for p in ("postgres://", "postgresql://"):
        if u.startswith(p):
            return "postgresql+psycopg://" + u[len(p):]
    return u


engine = create_engine(
    _url(os.getenv("DESAFIO_DATABASE_URL") or os.getenv("DATABASE_URL", "")),
    pool_pre_ping=True,
)
SessionDesafio = sessionmaker(bind=engine, autoflush=False)


class BaseDesafio(DeclarativeBase):
    pass


class Envio(BaseDesafio):
    __tablename__ = "desafio_envios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(60))
    nombre_norm: Mapped[str] = mapped_column(String(60), unique=True)
    puntos: Mapped[int] = mapped_column(Integer, index=True)
    aciertos: Mapped[int] = mapped_column(Integer)
    errores: Mapped[int] = mapped_column(Integer)
    puntos_f1: Mapped[int] = mapped_column(Integer)
    puntos_f2: Mapped[int] = mapped_column(Integer)
    puntos_f3: Mapped[int] = mapped_column(Integer)
    respuestas: Mapped[dict] = mapped_column(JSON)
    detalle: Mapped[dict] = mapped_column(JSON)
    creado_en: Mapped["DateTime"] = mapped_column(DateTime, default=ahora)


BaseDesafio.metadata.create_all(bind=engine)


def get_db():
    db = SessionDesafio()
    try:
        yield db
    finally:
        db.close()


class EnvioIn(BaseModel):
    nombre: str
    respuestas: dict[str, list[str]]


publico = APIRouter()   # sin login
privado = APIRouter()   # main.py le agrega el login


# ---------- Páginas y datos públicos ----------
@publico.get("/desafio")
def pagina_desafio():
    return FileResponse(BASE_DIR / "static" / "desafio.html")


@publico.get("/desafio/config")
def config():
    return {
        "fundas": [{"id": k, "titulo": f["titulo"]} for k, f in FUNDAS.items()],
        "opciones": OPCIONES,
    }


@publico.post("/desafio/enviar")
def enviar(datos: EnvioIn, db: Session = Depends(get_db)):
    nombre = " ".join(datos.nombre.split())
    if not 1 <= len(nombre) <= 60:
        raise HTTPException(400, "Escribí tu nombre (hasta 60 caracteres)")
    if set(datos.respuestas) - set(FUNDAS):
        raise HTTPException(400, "Respuesta inválida")
    validas = set(OPCIONES)
    orden = OPCIONES.index

    detalle, respuestas, puntos_f = {}, {}, {}
    ac_total = er_total = 0
    for fid, f in FUNDAS.items():
        marcadas = set(datos.respuestas.get(fid, []))
        if not marcadas <= validas:
            raise HTTPException(400, "Respuesta inválida")
        correctas = set(f["correctos"])
        ac, er, om = marcadas & correctas, marcadas - correctas, correctas - marcadas
        pts = len(ac) * PTS_ACIERTO - len(er) * PTS_ERROR
        puntos_f[fid] = pts
        ac_total += len(ac)
        er_total += len(er)
        respuestas[fid] = sorted(marcadas, key=orden)
        detalle[fid] = {
            "titulo": f["titulo"],
            "puntos": pts,
            "aciertos": sorted(ac, key=orden),
            "errores": sorted(er, key=orden),
            "omitidos": sorted(om, key=orden),
        }

    norm = nombre.casefold()
    if db.scalar(select(Envio.id).where(Envio.nombre_norm == norm)):
        raise HTTPException(409, "Ese nombre ya participó. Agregá tu apellido o legajo.")
    db.add(Envio(
        nombre=nombre, nombre_norm=norm,
        puntos=sum(puntos_f.values()), aciertos=ac_total, errores=er_total,
        puntos_f1=puntos_f["1"], puntos_f2=puntos_f["2"], puntos_f3=puntos_f["3"],
        respuestas=respuestas, detalle=detalle,
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ese nombre ya participó. Agregá tu apellido o legajo.")
    return {"ok": True}   # el puntaje NO se muestra al participante


# ---------- Resultados (con login) ----------
@publico.get("/resultados")
def pagina_resultados(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login")
    return FileResponse(BASE_DIR / "static" / "resultados.html")


def _ordenados(db: Session):
    return db.scalars(
        select(Envio).order_by(Envio.puntos.desc(), Envio.creado_en.asc(), Envio.id.asc())
    ).all()


@privado.get("/resultados/datos")
def resultados_datos(db: Session = Depends(get_db)):
    filas = [
        {
            "posicion": i,
            "nombre": e.nombre,
            "total": e.puntos,
            "aciertos": e.aciertos,
            "errores": e.errores,
            "f1": e.puntos_f1,
            "f2": e.puntos_f2,
            "f3": e.puntos_f3,
            "fecha": e.creado_en.strftime("%d/%m %H:%M"),
            "detalle": e.detalle,
        }
        for i, e in enumerate(_ordenados(db), start=1)
    ]
    return {"maximo": MAXIMO, "filas": filas}


@privado.delete("/resultados/borrar")
def borrar_todo(db: Session = Depends(get_db)):
    n = db.scalar(select(func.count(Envio.id)))
    db.execute(text("TRUNCATE TABLE desafio_envios RESTART IDENTITY"))
    db.commit()
    return {"borrados": n}

def _seguro(v) -> str:
    # evita que Excel interprete un nombre como fórmula
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@privado.get("/resultados/csv")
def resultados_csv(db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([
        "Posicion", "Nombre", "Puntos totales", "Aciertos", "Errores",
        "Puntos Funda 1", "Puntos Funda 2", "Puntos Funda 3",
        "Respuestas Funda 1", "Respuestas Funda 2", "Respuestas Funda 3", "Fecha",
    ])
    for i, e in enumerate(_ordenados(db), start=1):
        w.writerow([
            i, _seguro(e.nombre), e.puntos, e.aciertos, e.errores,
            e.puntos_f1, e.puntos_f2, e.puntos_f3,
            *[_seguro(" | ".join(e.respuestas.get(k, []))) for k in ("1", "2", "3")],
            e.creado_en.strftime("%d/%m/%Y %H:%M"),
        ])
    return Response(
        "\ufeff" + buf.getvalue(),   # BOM: Excel respeta tildes y ñ
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="resultados_funda_detective.csv"'},
    )