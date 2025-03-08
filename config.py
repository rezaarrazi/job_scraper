from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database Configuration
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/job_scraper"  # Note the +asyncpg

    # API Keys
    GOOGLE_API_KEY: str
    FIRECRAWL_API_KEY: str
    
    # Thread Pool Configuration
    THREAD_POOL_SIZE: int = 10
    
    # Scraping Configuration
    BATCH_SIZE: int = 50
    MAX_CONCURRENT: int = 10
    
    class Config:
        env_file = ".env"
        case_sensitive = False  # This makes the env var names case-insensitive

settings = Settings()