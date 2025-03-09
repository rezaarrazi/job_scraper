from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from enum import Enum
from uuid import UUID

class RegexPatternSchema(BaseModel):
    pattern: str
    explanation: str

class JobSchema(BaseModel):
    company_name: str
    company_industry: Optional[str] = None
    job_title: str
    job_type: Optional[str] = None
    location: Optional[str] = None
    description: str
    responsibilities: Optional[List[str]] = []
    requirements: Optional[List[str]] = []
    benefits: Optional[List[str]] = []

class JobDetailPatternSchema(BaseModel):
    company_name_regex_pattern: RegexPatternSchema
    company_industry_regex_pattern: RegexPatternSchema
    job_title_regex_pattern: RegexPatternSchema
    job_type_regex_pattern: RegexPatternSchema
    location_regex_pattern: RegexPatternSchema
    description_regex_pattern: RegexPatternSchema
    responsibilities_regex_pattern: RegexPatternSchema
    requirements_regex_pattern: RegexPatternSchema
    benefits_regex_pattern: RegexPatternSchema

class JobResponse(BaseModel):
    id: UUID
    job_url: str
    company_name: str
    job_title: str
    location: str
    created_at: datetime
    updated_at: datetime

class PaginationType(str, Enum):
    NONE = "none"
    PAGE_NUMBERS = "page_numbers"
    LOAD_MORE = "load_more"
    INFINITE_SCROLL = "infinite_scroll"

class PaginationPattern(BaseModel):
    type: PaginationType
    next_page_pattern: RegexPatternSchema
    load_more_selector: str
    page_param: str
    items_per_page: int

class JobSiteAnalysis(BaseModel):
    job_url_pattern: RegexPatternSchema
    pagination: PaginationPattern 

class ScrapeResponse(BaseModel):
    task_id: str

class ScrapeStatusResponse(BaseModel):
    task_id: str
    status: str
    progress: int
    error: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None 