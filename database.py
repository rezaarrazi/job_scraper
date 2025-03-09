from sqlalchemy import create_engine, Column, String, JSON, DateTime, Integer, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os
from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import UUID
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from config import settings

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/job_scraper')

# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=True,
    future=True
)

# Create async session factory
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()

class JobSite(Base):
    __tablename__ = "job_sites"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url = Column(String, unique=True, index=True)
    regex_patterns = Column(JSON)  # Store job_site regex patterns
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    pagination_type = Column(String)
    pagination_info = Column(JSON)
    
    jobs = relationship("Job", back_populates="job_site", cascade="all, delete-orphan", passive_deletes=True)

class Job(Base):
    __tablename__ = "jobs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_site_id = Column(UUID(as_uuid=True), ForeignKey("job_sites.id", ondelete="CASCADE"), nullable=False)
    job_url = Column(String, unique=True, index=True)
    company_name = Column(String)
    company_industry = Column(String, nullable=True)
    job_title = Column(String)
    job_type = Column(String, nullable=True)
    location = Column(String, nullable=True)
    description = Column(String)
    responsibilities = Column(JSON, nullable=True)  # Store as JSON array
    requirements = Column(JSON, nullable=True)      # Store as JSON array
    benefits = Column(JSON, nullable=True)          # Store as JSON array
    regex_patterns = Column(JSON, nullable=True)    # Store job_listing regex patterns
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    job_site = relationship("JobSite", back_populates="jobs")

async def init_db():
    async with engine.begin() as conn:
        # Only create tables that don't exist
        await conn.run_sync(Base.metadata.create_all)

# Optional: Create a separate function for database reset (development only)
async def reset_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

# Dependency
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()