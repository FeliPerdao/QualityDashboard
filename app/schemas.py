from pydantic import BaseModel


class RegistroIn(BaseModel):
    isla_id: int
    defecto_ids: list[int] = []
    retrabajada: bool = False
    
class RetrabajoIn(BaseModel):
    proyecto: str
    funda: str
    defecto: str
    detalle: str | None = None