from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

class RegexPatternSchema(BaseModel):
    pattern: str
    explanation: str

class JobCardPatternSchema(BaseModel):
    job_url_pattern: RegexPatternSchema

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
    id: int
    job_url: str
    company_name: str
    job_title: str
    location: Optional[str]
    created_at: datetime
    updated_at: datetime 