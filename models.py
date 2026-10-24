from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship

from database import Base


class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    category = Column(String(50), nullable=True)  # e.g., Site, Office, Department

    devices = relationship("Device", back_populates="location")


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    device_type = Column(String(50), nullable=False)  # laptop, router, printer, etc.
    serial_number = Column(String(100), nullable=True)
    barcode = Column(String(100), nullable=True, unique=True)
    status = Column(String(20), nullable=False, default="Active")  # Active, In Repair, Retired, Loaned

    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)

    location = relationship("Location", back_populates="devices")

    notes = Column(String(500), nullable=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=False, unique=True)
    full_name = Column(String(100), nullable=True)
    role = Column(String(20), nullable=False, default="viewer")  # manager, viewer
    password_hash = Column(String(255), nullable=False)
