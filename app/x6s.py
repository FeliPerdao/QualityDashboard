from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Table,
    UniqueConstraint, and_, case, cast, func, select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .database import Base, SessionLocal, get_db
from .models import Isla, ahora

BASE_DIR = Path(__file__).parent
CARPETA = BASE_DIR / "defectos_x6s"
ARCHIVOS = {
    "Respaldo delantero": "respaldo_delantero.txt",
    "Respaldo trasero": "respaldo_trasero.txt",
    "Asiento delantero": "asiento_delantero.txt",
    "Asiento trasero": "asiento_trasero.txt",
}

x6s_registro_defectos = Table(
    "x6s_registro_defectos",
    Base.metadata,
    Column("registro_id", ForeignKey("x6s_registros.id", ondelete="CASCADE"), primary_key=True),
    Column("defecto_id", ForeignKey("x6s_defectos.id"), primary_key=True),
)


class X6SDefecto(Base):
    __tablename__ = "x6s_defectos"
    __table_args__ = (UniqueConstraint("isla_id", "seccion", "nombre"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    isla_id: Mapped[int] = mapped_column(ForeignKey("islas.id"))
    seccion: Mapped[str] = mapped_column(String(60))
    nombre: Mapped[str] = mapped_column(String(120))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    orden: Mapped[int] = mapped_column(Integer, default=0)


class X6SRegistro(Base):
    __tablename__ = "x6s_registros"

    id: Mapped[int] = mapped_column(primary_key=True)
    isla_id: Mapped[int] = mapped_column(ForeignKey("islas.id"))
    con_defecto: Mapped[bool] = mapped_column(Boolean, default=False)
    retrabajada: Mapped[bool] = mapped_column(Boolean, default=False)
    creado_en: Mapped["DateTime"] = mapped_column(DateTime, default=ahora, index=True)
    defectos: Mapped[list[X6SDefecto]] = relationship(secondary=x6s_registro_defectos)


class RegistroIn(BaseModel):
    isla_id: int
    defecto_ids: list[int] = []
    retrabajada: bool = False


# ---------- Defectos desde txt (uno por isla) ----------
def _leer(archivo: Path):
    items, seccion = [], "General"
    for l in archivo.read_text(encoding="utf-8-sig").splitlines():
        l = l.strip()
        if not l or l.startswith("#"):
            continue
        if l.startswith("[") and l.endswith("]"):
            seccion = l[1:-1].strip() or "General"
            continue
        if (seccion, l) not in items:
            items.append((seccion, l))
    return items


def sincronizar_isla(s: Session, isla: Isla):
    nombre_archivo = ARCHIVOS.get(isla.nombre)
    archivo = CARPETA / nombre_archivo if nombre_archivo else None
    if not archivo or not archivo.exists():
        return
    items = _leer(archivo)
    existentes = {
        (d.seccion, d.nombre): d
        for d in s.scalars(select(X6SDefecto).where(X6SDefecto.isla_id == isla.id))
    }
    for orden, clave in enumerate(items):
        d = existentes.get(clave)
        if d:
            d.activo, d.orden = True, orden
        else:
            s.add(X6SDefecto(isla_id=isla.id, seccion=clave[0], nombre=clave[1], orden=orden, activo=True))
    for clave, d in existentes.items():
        if clave not in items:
            d.activo = False
    s.commit()


def sincronizar_todo():
    with SessionLocal() as s:
        for isla in s.scalars(select(Isla)):
            sincronizar_isla(s, isla)


# ---------- Cálculos (iguales al dashboard principal) ----------
def calc_ippm(defectuosas, liberadas):
    return round(defectuosas / liberadas * 1_000_000, 1) if liberadas else None


def armar(id_, nombre, ok, defecto, retrab):
    return {
        "id": id_, "isla": nombre, "ok": ok, "con_defecto": defecto,
        "retrabajadas": retrab, "controladas": ok + defecto + retrab,
        "en_retrabajo": max(0, defecto - retrab),
        "ippm": calc_ippm(defecto, ok + retrab),
    }


def contar_acumulado(db: Session, isla_id: int):
    n_def = db.scalar(select(func.count(X6SRegistro.id)).where(
        X6SRegistro.isla_id == isla_id, X6SRegistro.con_defecto.is_(True)))
    n_ret = db.scalar(select(func.count(X6SRegistro.id)).where(
        X6SRegistro.isla_id == isla_id, X6SRegistro.retrabajada.is_(True)))
    return n_def, n_ret


# ---------- Rutas ----------
def requiere_login(request: Request):
    if not request.session.get("auth"):
        raise HTTPException(401, "No autenticado")


publico = APIRouter()
api = APIRouter(prefix="/x6s/api", dependencies=[Depends(requiere_login)])


@publico.get("/x6s")
def pagina(request: Request):
    if not request.session.get("auth"):
        return RedirectResponse("/login")
    return FileResponse(BASE_DIR / "static" / "x6s.html")


@api.get("/islas")
def listar_islas(db: Session = Depends(get_db)):
    islas = db.scalars(select(Isla).where(Isla.activa).order_by(Isla.id)).all()
    return [{"id": i.id, "nombre": i.nombre} for i in islas]


@api.get("/defectos")
def listar_defectos(isla_id: int, db: Session = Depends(get_db)):
    isla = db.get(Isla, isla_id)
    if not isla:
        raise HTTPException(404, "La isla no existe")
    sincronizar_isla(db, isla)
    ds = db.scalars(
        select(X6SDefecto)
        .where(X6SDefecto.isla_id == isla_id, X6SDefecto.activo)
        .order_by(X6SDefecto.orden)
    ).all()
    grupos = {}
    for d in ds:
        grupos.setdefault(d.seccion, []).append({"id": d.id, "nombre": d.nombre})
    return [{"seccion": s, "defectos": v} for s, v in grupos.items()]


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
        db.scalars(select(X6SDefecto).where(
            X6SDefecto.id.in_(ids), X6SDefecto.isla_id == datos.isla_id, X6SDefecto.activo)).all()
        if ids else []
    )
    if len(defectos) != len(ids):
        raise HTTPException(400, "Algún defecto no existe o no corresponde a esta isla")
    r = X6SRegistro(
        isla_id=datos.isla_id, con_defecto=bool(defectos),
        retrabajada=datos.retrabajada, defectos=defectos,
    )
    db.add(r)
    db.commit()
    return {"id": r.id, "con_defecto": r.con_defecto, "retrabajada": r.retrabajada}


@api.delete("/registros/{registro_id}")
def borrar_registro(registro_id: int, db: Session = Depends(get_db)):
    r = db.get(X6SRegistro, registro_id)
    if not r:
        raise HTTPException(404, "El registro no existe")
    if r.con_defecto:
        n_def, n_ret = contar_acumulado(db, r.isla_id)
        if n_def - 1 < n_ret:
            raise HTTPException(
                409, "No se puede deshacer: hay fundas retrabajadas que dependen de esa funda con defecto")
    db.delete(r)
    db.commit()
    return {"borrado": registro_id}


@api.get("/ippm")
def ippm(desde: date | None = None, hasta: date | None = None, db: Session = Depends(get_db)):
    desde = desde or ahora().date()
    hasta = hasta or desde
    if desde > hasta:
        raise HTTPException(400, "Rango de fechas inválido")
    R = X6SRegistro
    dia = cast(R.creado_en, Date)

    es_ok = case((and_(R.con_defecto.is_(False), R.retrabajada.is_(False)), 1), else_=0)
    es_def = case((R.con_defecto.is_(True), 1), else_=0)
    es_ret = case((R.retrabajada.is_(True), 1), else_=0)

    filas = db.execute(
        select(
            Isla.id, Isla.nombre,
            func.coalesce(func.sum(es_ok), 0),
            func.coalesce(func.sum(es_def), 0),
            func.coalesce(func.sum(es_ret), 0),
        )
        .outerjoin(R, and_(R.isla_id == Isla.id, dia.between(desde, hasta)))
        .where(Isla.activa)
        .group_by(Isla.id, Isla.nombre)
        .order_by(Isla.id)
    ).all()
    islas = [armar(i, n, int(o), int(d), int(r)) for i, n, o, d, r in filas]
    total = armar(
        0, "Total",
        sum(x["ok"] for x in islas),
        sum(x["con_defecto"] for x in islas),
        sum(x["retrabajadas"] for x in islas),
    )

    filas_p = db.execute(
        select(R.isla_id, X6SDefecto.nombre, func.count(x6s_registro_defectos.c.registro_id))
        .select_from(x6s_registro_defectos)
        .join(R, R.id == x6s_registro_defectos.c.registro_id)
        .join(X6SDefecto, X6SDefecto.id == x6s_registro_defectos.c.defecto_id)
        .where(dia.between(desde, hasta))
        .group_by(R.isla_id, X6SDefecto.nombre)
    ).all()
    for x in islas:
        x["defectos"] = [{"defecto": n, "cantidad": int(c)} for i, n, c in filas_p if i == x["id"]]
    catalogo = list(dict.fromkeys(
        db.scalars(select(X6SDefecto.nombre).order_by(X6SDefecto.orden, X6SDefecto.id))))

    horas = list(range(5, 15))
    hora = func.extract("hour", R.creado_en)
    filas_h = db.execute(
        select(R.isla_id, hora, func.count(R.id))
        .where(dia.between(desde, hasta), R.con_defecto.is_(False))
        .group_by(R.isla_id, hora)
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
        "desde": desde, "hasta": hasta, "total": total, "islas": islas,
        "catalogo_defectos": catalogo, "por_hora": por_hora,
    }