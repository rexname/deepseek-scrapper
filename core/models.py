from sqlalchemy import Column, String, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base
import uuid

class Account(Base):
    __tablename__ = "accounts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, index=True)
    password = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    sessions = relationship("Session", back_populates="account", cascade="all, delete-orphan")
    chats_history = relationship("Chat", back_populates="account")

class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, unique=True, index=True) # ID session untuk Browserless
    account_email = Column(ForeignKey("accounts.email"), nullable=True)
    site_name = Column(String, default="deepseek")
    storage_state = Column(JSON, nullable=True) # JSON Playwright storage state
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    account = relationship("Account", back_populates="sessions")
    chats = relationship("Chat", back_populates="session", cascade="all, delete-orphan")

class Chat(Base):
    __tablename__ = "chats"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_id = Column(String, unique=True, index=True) # ID chat dari URL DeepSeek
    session_id = Column(ForeignKey("sessions.session_id"))
    account_email = Column(ForeignKey("accounts.email"), nullable=True) # Pemilik chat
    title = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    session = relationship("Session", back_populates="chats")
    account = relationship("Account", back_populates="chats_history") # Relationship ke account
    messages = relationship("Message", back_populates="chat", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_id = Column(ForeignKey("chats.id"))
    role = Column(String) # 'user' or 'ai'
    content = Column(Text)
    image_url = Column(String, nullable=True) # Path file lokal atau URL
    created_at = Column(DateTime, default=datetime.utcnow)
    
    chat = relationship("Chat", back_populates="messages")
