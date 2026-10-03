from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, Table, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


def ahora():
    return datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).replace(tzinfo=None)


registro_defectos = Table(
    "registro_defectos",
    Base.metadata,
    Column("registro_id", ForeignKey("registros.id", ondelete="CASCADE"), primary_key=True),
    Column("defecto_id", ForeignKey("defectos.id"), primary_key=True),
)


class Isla(Base):
    __tablename__ = "islas"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(50), unique=True)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)


class Defecto(Base):
    __tablename__ = "defectos"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80), unique=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    orden: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80), unique=True)


class Registro(Base):
    __tablename__ = "registros"

    id: Mapped[int] = mapped_column(primary_key=True)
    isla_id: Mapped[int] = mapped_column(ForeignKey("islas.id"))
    con_defecto: Mapped[bool] = mapped_column(Boolean, default=False)
    retrabajada: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora, index=True)
    defectos: Mapped[list[Defecto]] = relationship(secondary=registro_defectos)