import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

load_dotenv()

url = os.getenv("DATABASE_URL", "")
for prefijo in ("postgres://", "postgresql://"):
    if url.startswith(prefijo):
        url = "postgresql+psycopg://" + url[len(prefijo):]
        break

engine = create_engine(url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()