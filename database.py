from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

DATABASE_URL = "sqlite:///./calories.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit = False, autoflush=False, bind=engine)

Base = declarative_base()

class CalorieLog(Base):
    __tablename__ = "logs"
    id = Column(Integer, primary_key=True, index=True)
    user_phone = Column(String)
    food_item = Column(String)
    calories = Column(Integer)
    protein = Column(Integer, default=0)
    carbs = Column(Integer, default=0)
    fats = Column(Integer, default=0)
    is_exercise = Column(Integer, default=0) # 0 for Food, 1 for Exercise
    timestamp = Column(DateTime, default=datetime.utcnow)

class WaterLog(Base):
    __tablename__ = "water_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_phone = Column(String)
    glasses = Column(Integer, default=1)
    timestamp = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
