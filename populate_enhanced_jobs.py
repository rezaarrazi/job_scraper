import os
import openai
from google import genai
import pandas as pd
from sqlalchemy import create_engine, text
from cuid import cuid
import json
import pickle
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from utils.logger import setup_logger
from utils.ai_client import AIClient

import logging
from dotenv import load_dotenv
from tqdm import tqdm
from datetime import datetime
from rapidfuzz import process, fuzz
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv()

# --- CONFIG ---
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")  # Replace with your actual key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Replace with your actual key

# Create backup directory
BACKUP_DIR = Path("data/backups/enhanced_jobs")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# Setup logger
logger = setup_logger(__name__, level=logging.DEBUG)

class SkillTag(BaseModel):
    required_skills_tag: List[str]
    preferred_skills_tag: List[str]

# --- Schema Definition ---
class JobExtraction(BaseModel):
    job_title: str
    simplified_job_title: str
    simplified_job_title_standardized: str
    company_name: str
    company_logo_url: str
    contract_type: str
    location: str
    experience_level: str
    min_experience: int
    max_experience: int
    description: str
    responsibilities: List[str]
    required_profile: List[str]
    preferred_profile: List[str]
    skills_tag: SkillTag
    tech_stack: List[str]
    language_requirements: List[str]
    work_arrangement: str
    linkedin_job_url: Optional[str]
    benefits: List[str]
    salary_range: str
    url: str
    is_external: bool
    minimum_education_level: str

JOB_EXTRACTION_PROMPT = '''
You are an expert assistant tasked with extracting structured data from job postings.
Given a full job description, extract and format the following fields clearly and detailly.

### Field Guidelines:

1. **jobTitle**
   * The official title of the position in correct and clean formatting
   * Should be specific and match the company's terminology
   * Always translate to English
   * If not stated, try to infer from the job description
   * Example: `"Senior Software Engineer"`

2. **simplifiedJobTitle**
    * Translate the job title to English.
    * Format in **Title Case** (e.g., "Product Manager"), but keep known abbreviations (e.g., CTO, CRM) in **UPPERCASE**.
    * Remove any mention of:
        - Seniority (e.g., "Senior", "Junior")
        - Gender (e.g., "m/w/d")
        - Location (e.g., "Jakarta", "Surabaya")
        - Contract type or employment terms (e.g., "Freelance", "Part-time", "Work-study")

3. **companyName**
   * The company offering the job
   * Use the name mentioned in the job description or job metadata
   * Example: `"Tokopedia"`

4. **companyLogoUrl**
   * The URL of the company logo
   * If not stated, leave it empty like `""`

5. **contractType**
   * Strictly select from the following list:
        - `"Full-Time"` : for full-time or permanent positions
        - `"Part-Time"` : for part-time positions
        - `"Contract"` : for contract or temporary positions
        - `"Freelance"` : for freelance positions
        - `"Internship"` : for internship positions
        - `"Other"` : for other contract types
   * If not stated, infer based on context, default to `"Full-time"`

6. **location**
   * Format: `"City, State/Province, Country"`
   * If missing, infer from job or company context

7. **experienceLevel**
   * Reformulate the experience level from the job title and description, select from the following:
        - `"Entry-Level"`
        - `"Mid-Level"`  
        - `"Senior-Level"`  
        - `"Lead"`  
        - `"Director"`  
        - `"Executive"`
   * If not stated, infer from title and description, default to `"Entry-Level"`

8. **minExperience**
   * Integer: minimum required years of experience
   * If not stated, infer:
     * Entry: 0-2
     * Mid: 2-5
     * Senior: 5-10
     * Lead+: 8-15

9. **maxExperience**
    * Integer: maximum years of experience
    * Should be higher or equal to minExperience
    * Leave it empty if not stated

10. **description**
    * A detailed description of the job role, including its purpose, scope, and expectations.
    * Translate to English if needed
    * Highlight role purpose and expectations

11. **responsibilities**
    * A structured list outlining the key duties and responsibilities associated with this role.
    * Use action-oriented statements
    * If not explicitly listed, extract from job body

12. **requiredProfile**
    * List of essential qualifications or skills
    * Can include:
      * Technical skills (e.g., Java, SQL)
      * Soft skills (e.g., collaboration)
      * Education (e.g., Bachelor's in Computer Science)
      * Certifications (e.g., AWS Certified Developer)
    * Elaborate each item in the list
    * If not listed, infer based on job description, responsibilities and role type
    * Try to not leave it empty

13. **preferredProfile**
    * A list of additional, nice-to-have skills that would be beneficial but are not mandatory. 
    * Includes:
      * Advanced degrees
      * Extra certifications or tools
      * Domain knowledge (e.g., fintech, e-commerce)
    * Elaborate each item in the list
    * If not stated, infer from context
    * Try to not leave it empty

14. **skillsTag**
    Extract two separate lists of **skills** from the job description:

    #### A. `requiredSkillsTag`

    * Core **skills, competencies, or knowledge areas** that are explicitly required.
    * Typically found in `jobTitle`, `requiredProfile`, responsibilities, or phrased as must-have.
    * Include:

    * Domain skills (e.g., `Credit Risk Modeling`, `Customer Acquisition`)
    * Core abilities (e.g., `Data Analysis`, `Communication`, `Problem Solving`)
    * Strongly emphasized soft skills (e.g., `Stakeholder Management`, `Collaboration`)

    #### B. `preferredSkillsTag`

    * **Nice-to-have** or optional skills.
    * Found in `preferredProfile`, bonus qualifications, or supportive context.
    * Include:

    * Strategic or cross-functional skills (e.g., `Product Thinking`, `Leadership`)
    * Industry familiarity (e.g., `Healthcare Analytics`, `AI Ethics`)
    * Non-essential soft skills or niche expertise

    #### General Guidelines:

    * Focus only on **skills**, not tools or libraries
    * Avoid vague words like "technologies" or "systems"
    * Ensure **distinct, non-overlapping** lists

15. **techStack**
    Extract a **single list** of all **technologies, tools, programming languages, libraries, and platforms** mentioned in the job description.

    #### techStack

    * Include:

    * Programming languages (e.g., `Python`, `SQL`, `Java`)
    * ML/AI libraries (e.g., `TensorFlow`, `Scikit-Learn`, `LangChain`)
    * DevOps/infra tools (e.g., `Docker`, `Kubernetes`, `Git`)
    * Cloud and analytics platforms (e.g., `AWS`, `Google BigQuery`, `Snowflake`)

    #### General Guidelines:

    * Combine both required and preferred tools into one flat list
    * Do not include abstract skills — focus only on tangible tools/libraries/platforms
    * Maximize the number of **distinct, relevant** tech stack items

16. **languageRequirements**
    * Required spoken/written language(s)
    * If not stated:
      * Use language of the posting
      * Infer from job location
      * If job is in Indonesia, default to `["Bahasa Indonesia"]`
      * If international, use `["English"]` or both

17. **workArrangement**
    * One of: `"Remote"`, `"On-site"`, `"Hybrid"`
    * Infer if missing using clues like job title or phrases like "work from home"
    * Default to `"On-site"` if not stated

18. **linkedinJobUrl**
    * The full URL of the job if posted on LinkedIn
    * If not stated, leave it empty like `""`

19. **benefits**
    * List of benefits provided by the company, select from the following categories:
        - `"Supplemental Insurance"`: (vision, dental, mental health, HSA/FSA)
        - `"Wellness Perks"`: (gym membership, wellness stipends, on-site fitness)
        - `"Remote Work Support"`: (WFH option, home office allowance, internet stipend)
        - `"Time Off"`: (unlimited PTO, recharge days, sabbaticals, volunteer days)
        - `"Financial Incentives"`: (bonuses, profit sharing, stock options/equity grants)
        - `"Commute & Relocation"`: (commuter allowance, relocation assistance, visa sponsorship)
        - `"Learning & Development"`: (L&D budget, tuition reimbursement, paid certifications, mentorship)
        - `"Parental & Family Support"`: (extended leave, fertility/adoption aid, childcare support)
        - `"Food & Office Perks"`: (free meals/snacks, pet-friendly office, retreats)
        - `"Equity & Ownership Benefits"`: (stock options, RSUs, employee stock purchase plans) 
    * If not stated, leave it empty like `[]`

20. **salaryRange**
    * Salary range offered
    * Example: `"$X,XXX - $X,XXX per year/month/hour"`
    * `"not specified"` (if no salary is mentioned)
    * `"Use the currency of the job posting."`
    * `"Try to use symbols for the currency."`
    * `"Always be precise with the number of zeros."`

21. **minimumEducationLevel**
    * Minimum education level required for the job
    * Select from the following:
        - `"No Formal Education Required"`  
        - `"High School Diploma"`  
        - `"Associate Degree"`  
        - `"Bachelor's Degree"`  
        - `"Master's Degree"`  
        - `"PhD or Equivalent"`
    
22. **url**
    * Any external job page or application URL

23. **isExternal**
    * Always set to `false`

---

Make sure your result is in **valid JSON**, with all arrays using proper brackets `[]`, and strings wrapped in quotes. please use the basic JSON syntax and do NOT include any extra whitespace or tabs for alignment.

Input:
{job_text}
'''


engine = create_engine(
    DATABASE_URL,
    pool_size=20,  # Increase base pool size
    max_overflow=30,  # Increase overflow
    pool_timeout=60,  # Increase timeout
    pool_recycle=3600,  # Recycle connections every hour
    echo=False
)

gemini_client = AIClient(provider='gemini', api_key=GEMINI_API_KEY)
openai_client = AIClient(provider='openai', api_key=OPENAI_API_KEY)

@retry(
    stop=stop_after_attempt(3),  # Maximum 3 retries
    wait=wait_exponential(multiplier=1, min=4, max=10),  # Wait between 4-10 seconds, increasing exponentially
    retry=retry_if_exception_type((Exception,)),  # Retry on any exception
    before_sleep=lambda retry_state: logger.warning(f"Retrying LLM call after error. Attempt {retry_state.attempt_number}/3")
)
def call_llm(job_text):
    try:
        prompt = JOB_EXTRACTION_PROMPT.format(job_text=job_text)
        
        response = gemini_client.generate_text(
                    model='gemini-2.5-flash-preview-05-20',
                    prompt=prompt,
                    response_schema=JobExtraction,
                    config={
                        'response_mime_type': 'application/json',
                        'max_output_tokens': 65535,
                        'temperature': 0.2
                    },
                    parse_response=True
                )
        
        return response
    except Exception as e:
        logger.error(f"❌ LLM Error: {str(e)}")
        raise  # Re-raise the exception to trigger retry

# --- Embedding Helpers ---
def generate_embedding(text: str):
    try:
        response = openai_client.generate_embedding(text, model="text-embedding-ada-002")
        return response
    except Exception as e:
        print(f"❌ Embedding error: {e}")
        return None

def process_skill_embeddings(conn, skills: List[str]) -> Dict[str, List[float]]:
    """
    Process a list of skills, generating and storing embeddings for each one.
    Returns a dictionary mapping skills to their embeddings.
    """
    skill_embeddings = {}
    
    for skill in skills:
        if not skill:
            continue
            
        # Check if embedding already exists
        existing = conn.execute(text("""
            SELECT "embedding" FROM "SkillEmbedding" WHERE "skill" = :skill
        """), {"skill": skill}).fetchone()
        
        if existing:
            skill_embeddings[skill] = existing[0]
            continue
            
        # Generate new embedding
        embedding = generate_embedding(skill)
        if not embedding:
            logger.warning(f"⚠️ Failed to generate embedding for skill: {skill}")
            continue
            
        # Store the embedding
        try:
            conn.execute(text("""
                INSERT INTO "SkillEmbedding" ("id", "skill", "embedding", "createdAt", "updatedAt")
                VALUES (:id, :skill, :embedding, NOW(), NOW())
                ON CONFLICT ("skill") DO UPDATE SET
                    "embedding" = EXCLUDED."embedding",
                    "updatedAt" = NOW()
            """), {
                "id": cuid(),
                "skill": skill,
                "embedding": json.dumps(embedding)
            })
            conn.commit()
            skill_embeddings[skill] = embedding
        except Exception as e:
            conn.rollback()
            logger.error(f"❌ Error storing embedding for skill {skill}: {str(e)}")
            

    skill_embedding_list = []
    for skill, embedding in skill_embeddings.items():
        skill_embedding_list.append({
            "skill": skill,
            "embedding": embedding
        })
    return skill_embedding_list

def standardize_with_exact_match(value: str, reference_list: list, threshold: int = 80) -> str:
    """
    Standardize a value by first checking for exact match, then using fuzzy matching if no exact match is found.
    
    Args:
        value: The value to standardize
        reference_list: List of reference values to match against
        threshold: Minimum score for fuzzy matching (default: 80)
        
    Returns:
        Standardized value (exact match if found, fuzzy match if score > threshold, original value otherwise)
    """
    # First check for exact match
    if value in reference_list:
        return value
        
    # If no exact match, try fuzzy matching
    match, score, _ = process.extractOne(value, reference_list, scorer=fuzz.token_set_ratio)
    return match if match and score > threshold else value

def process_job_worker(row: Any, industries_candidates: List[str], job_titles: List[str], engine) -> Dict[str, Any]:
    """Worker function to process a single job"""
    connection = None
    try:
        job_raw_id = row.id
        data = row._asdict()
        data["linkedinJobUrl"] = f"https://www.linkedin.com{data['linkedinJobUrl']}"

        # Convert datetime objects to ISO format strings
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        
        text_to_send = json.dumps(data, indent=2)
        parsed = call_llm(text_to_send)
        if not parsed:
            logger.warning(f"⚠️ Skipped due to LLM error for job: {row.jobTitle}")
            return None
        
        parsed = parsed.model_dump(mode='json')

        company_industries = data['company_industry1'] if data['company_industry1'] else data['company_industry2']

        if company_industries:
            parsed['company_industries'] = company_industries.split(',')
        else:
            parsed['company_industries'] = []

        # Process contract type
        contract_type_candidates = ["Full-Time", "Part-Time", "Contract", "Freelance", "Internship"]
        standardized_contract_type = standardize_with_exact_match(parsed['contract_type'], contract_type_candidates)
        parsed['contract_type'] = standardized_contract_type

        # Process experience level
        experience_level_candidates = ["Entry-Level", "Mid-Level", "Senior-Level", "Lead", "Director", "Executive"]
        standardized_experience_level = standardize_with_exact_match(parsed['experience_level'], experience_level_candidates)
        parsed['experience_level'] = standardized_experience_level
        
        # Process job title
        standardized_job_title = standardize_with_exact_match(parsed["simplified_job_title"], job_titles)
        parsed['simplified_job_title_standardized'] = standardized_job_title

        # Process skill embeddings - get a fresh connection for this worker
        connection = engine.connect()
        required_skills = parsed.get("skills_tag", {}).get("required_skills_tag", [])
        preferred_skills = parsed.get("skills_tag", {}).get("preferred_skills_tag", [])
        
        # Generate embeddings for all skills
        required_skill_embeddings = process_skill_embeddings(connection, required_skills)
        preferred_skill_embeddings = process_skill_embeddings(connection, preferred_skills)
        
        # Add embeddings to the parsed data
        parsed['skillEmbeddings'] = {
            "requiredSkills": required_skill_embeddings,
            "preferredSkills": preferred_skill_embeddings
        }
        
        return {
            "job_raw_id": job_raw_id,
            "data": data,
            "parsed": parsed
        }
    except Exception as e:
        logger.error(f"❌ Error processing job {row.jobTitle}: {str(e)}")
        return None
    finally:
        # Ensure connection is properly closed
        if connection:
            try:
                connection.close()
            except Exception as e:
                logger.warning(f"⚠️ Error closing connection: {str(e)}")

def process_jobs_phase(batch_size: int = 100, max_batches: int = None) -> List[str]:
    """Phase 1: Process all jobs with LLM in paginated batches and save to backup files"""
    logger.info("🚀 Starting Phase 1: Paginated Job Processing")
    
    all_batch_ids = []
    current_page = 0
    total_processed = 0
    total_failed = 0
    
    with engine.connect() as conn:
        industries_candidates = pd.read_csv(os.path.join(os.getcwd(), 'data/output/industries/unique_industries.csv'))['industry'].tolist()
        # Load job titles from JSON
        job_categories_path = os.path.join(os.getcwd(), './data/output/job_category/jobright_job_categories.json')
        with open(job_categories_path, 'r') as f:
            job_categories = json.load(f)
        
        # Extract all job titles into a flat list
        job_titles = []
        for category in job_categories.values():
            for subcategory in category.values():
                job_titles.extend(subcategory)

        # First, get total count of unprocessed jobs
        total_unprocessed = conn.execute(text("""
            SELECT COUNT(*) 
            FROM "JobRaw" jr
            LEFT JOIN "Company" c ON jr."companyId" = c."id"
            LEFT JOIN "EnhancedJobDetail" ejd ON jr."id" = ejd."jobRawId"
            WHERE ejd."id" IS NULL and c."id" IS NOT NULL
        """)).scalar()
        
        logger.info(f"📊 Total unprocessed jobs: {total_unprocessed}")
        logger.info(f"📄 Processing in batches of {batch_size}")
        
        if max_batches:
            logger.info(f"🔒 Limited to maximum {max_batches} batches")
            
        while True:
            # Check if we've hit the max_batches limit
            if max_batches and current_page >= max_batches:
                logger.info(f"🔒 Reached maximum batch limit ({max_batches})")
                break
                
            # Get next batch of jobs with pagination
            offset = current_page * batch_size
            rows = conn.execute(text("""
                SELECT 
                    jr.*,
                    c."organizationName" as company_name,
                    csd."profilePhoto" as company_logo_url,
                    c."industries" as company_industry1,
                    csd."industries" as company_industry2,
                    csd."location" as company_location,
                    csd."companySize" as company_size,
                    csd."about" as company_about,
                    csd."type" as company_type,
                    csd."enhancedDescription" as company_enhanced_description
                FROM "JobRaw" jr
                LEFT JOIN "Company" c ON jr."companyId" = c."id"
                LEFT JOIN "CompanyScrapingdog" csd ON c."id" = csd."companyId"
                LEFT JOIN "EnhancedJobDetail" ejd ON jr."id" = ejd."jobRawId"
                WHERE ejd."id" IS NULL and c."id" IS NOT NULL
                ORDER BY jr."id"
                LIMIT :batch_size OFFSET :offset
            """), {"batch_size": batch_size, "offset": offset}).fetchall()
            
            # If no more jobs, we're done
            if not rows:
                logger.info("✅ No more jobs to process")
                break
                
            current_page += 1
            logger.info(f"📄 Processing batch {current_page} - Jobs {offset + 1} to {offset + len(rows)} ({len(rows)} jobs)")

            # Process jobs concurrently (LLM processing only)
            max_workers = 3  # Conservative for bandwidth management
            processed_jobs = []
            failed_jobs = 0

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all jobs to the executor
                futures = {
                    executor.submit(
                        process_job_worker,
                        row,
                        industries_candidates,
                        job_titles
                    ): row for row in rows
                }

                # Create progress bar for this batch
                with tqdm(total=len(rows), desc=f"🧠 Batch {current_page} LLM Processing", unit="job") as pbar:
                    for future in as_completed(futures):
                        row = futures[future]
                        try:
                            result = future.result()
                            if result:
                                processed_jobs.append(result)
                            else:
                                failed_jobs += 1
                        except Exception as e:
                            logger.error(f"❌ Error processing job {row.jobTitle}: {str(e)}")
                            failed_jobs += 1
                        finally:
                            pbar.update(1)
                            pbar.set_postfix(
                                processed=len(processed_jobs),
                                failed=failed_jobs
                            )

            # Save processed data to backup files for this batch
            if processed_jobs:
                batch_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                batch_id = f"{batch_timestamp}_batch_{current_page:03d}"
                backup_info = save_processed_data(processed_jobs, batch_id)
                all_batch_ids.append(batch_id)
                
                total_processed += len(processed_jobs)
                total_failed += failed_jobs
                
                logger.info(f"✅ Batch {current_page} complete! Processed: {len(processed_jobs)}, Failed: {failed_jobs}")
                logger.info(f"📁 Batch saved with ID: {batch_id}")
                logger.info(f"📊 Progress: {total_processed}/{total_unprocessed} total processed ({total_processed/total_unprocessed*100:.1f}%)")
                
                # Brief pause between batches to manage bandwidth
                time.sleep(2)
            else:
                logger.warning(f"⚠️ Batch {current_page}: No jobs were successfully processed")
                total_failed += failed_jobs

        # Summary
        logger.info(f"🎉 Phase 1 Complete!")
        logger.info(f"📊 Total batches processed: {len(all_batch_ids)}")
        logger.info(f"📊 Total jobs processed: {total_processed}")
        logger.info(f"📊 Total jobs failed: {total_failed}")
        logger.info(f"📁 Batch IDs: {all_batch_ids}")
        
        return all_batch_ids

def ingest_jobs_phase(batch_ids: List[str] = None, batch_id: str = None, from_backup: bool = False) -> tuple:
    """Phase 2: Ingest processed jobs to database (supports single batch or multiple batches)"""
    logger.info("🚀 Starting Phase 2: Database Ingestion")
    
    # Handle different input formats
    if batch_id and not batch_ids:
        batch_ids = [batch_id]
    elif not batch_ids and not batch_id:
        logger.error("❌ No batch_id or batch_ids provided for ingestion phase")
        return 0, 0
    
    total_successful = 0
    total_failed = 0
    
    for i, current_batch_id in enumerate(batch_ids, 1):
        logger.info(f"📦 Processing batch {i}/{len(batch_ids)}: {current_batch_id}")
        
        try:
            # Load from backup
            processed_jobs = load_processed_data(current_batch_id)
            
            # Ingest to database
            successful_jobs, failed_jobs = ingest_to_database(processed_jobs, engine)
            
            total_successful += successful_jobs
            total_failed += failed_jobs
            
            logger.info(f"✅ Batch {current_batch_id} complete! Successful: {successful_jobs}, Failed: {failed_jobs}")
            
            # Brief pause between batches
            if i < len(batch_ids):
                logger.info("⏳ Brief pause before next batch...")
                time.sleep(3)
                
        except Exception as e:
            logger.error(f"❌ Error processing batch {current_batch_id}: {str(e)}")
            continue
    
    logger.info(f"✅ Phase 2 Complete! Total - Successful: {total_successful}, Failed: {total_failed}")
    return total_successful, total_failed

def ingest_all_batches() -> tuple:
    """Ingest all available backup batches"""
    logger.info("🔍 Searching for all available backup batches...")
    
    # Find all backup files
    backup_files = list(BACKUP_DIR.glob("enhanced_jobs_raw_*.pkl"))
    
    if not backup_files:
        logger.warning("⚠️ No backup files found")
        return 0, 0
    
    # Extract batch IDs from filenames
    batch_ids = []
    for file_path in backup_files:
        # Extract batch_id from filename like "enhanced_jobs_raw_20250603_172230_batch_001.pkl"
        filename = file_path.stem
        if filename.startswith("enhanced_jobs_raw_"):
            batch_id = filename[len("enhanced_jobs_raw_"):]
            batch_ids.append(batch_id)
    
    batch_ids.sort()  # Process in chronological order
    logger.info(f"📁 Found {len(batch_ids)} batches: {batch_ids}")
    
    return ingest_jobs_phase(batch_ids=batch_ids, from_backup=True)

def list_available_batches():
    """List all available backup batches with details"""
    logger.info("📋 Available backup batches:")
    
    backup_files = list(BACKUP_DIR.glob("enhanced_jobs_raw_*.pkl"))
    
    if not backup_files:
        logger.info("   No backup files found")
        return []
    
    batch_info = []
    for file_path in backup_files:
        try:
            # Get file stats
            stat = file_path.stat()
            size_mb = stat.st_size / (1024 * 1024)
            modified = datetime.fromtimestamp(stat.st_mtime)
            
            # Extract batch_id
            filename = file_path.stem
            if filename.startswith("enhanced_jobs_raw_"):
                batch_id = filename[len("enhanced_jobs_raw_"):]
                
                # Try to load and get job count
                try:
                    with open(file_path, 'rb') as f:
                        jobs = pickle.load(f)
                    job_count = len(jobs)
                except:
                    job_count = "Unknown"
                
                batch_info.append({
                    "batch_id": batch_id,
                    "job_count": job_count,
                    "size_mb": size_mb,
                    "created": modified,
                    "file_path": file_path
                })
                
                logger.info(f"   {batch_id}: {job_count} jobs, {size_mb:.1f}MB, created {modified.strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as e:
            logger.warning(f"   Error reading {file_path}: {e}")
    
    return batch_info

def save_processed_data(processed_jobs: List[Dict], batch_id: str = None):
    """Save processed job data to backup files"""
    if not batch_id:
        batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Create DataFrames for different data types
    main_data = []
    embeddings_data = []
    
    for job in processed_jobs:
        # Main job data
        main_record = {
            "job_raw_id": job["job_raw_id"],
            "jobTitle": job["parsed"].get("job_title"),
            "simplifiedJobTitle": job["parsed"].get("simplified_job_title"),
            "simplifiedJobTitleStandardized": job["parsed"].get("simplified_job_title_standardized"),
            "companyName": job["parsed"].get("company_name"),
            "companyLogoUrl": job["data"]["company_logo_url"],
            "companyIndustries": json.dumps(job["parsed"].get("company_industries", [])),
            "companyType": job["data"]["company_type"],
            "companyDescription": job["data"]["company_enhanced_description"],
            "contractType": job["parsed"].get("contract_type"),
            "location": job["parsed"].get("location"),
            "experienceLevel": job["parsed"].get("experience_level"),
            "minExperience": job["parsed"].get("min_experience"),
            "maxExperience": job["parsed"].get("max_experience"),
            "description": job["parsed"].get("description"),
            "responsibilities": json.dumps(job["parsed"].get("responsibilities", [])),
            "requiredProfile": json.dumps(job["parsed"].get("required_profile", [])),
            "preferredProfile": json.dumps(job["parsed"].get("preferred_profile", [])),
            "skillsTag": json.dumps({
                "requiredSkillsTag": job["parsed"].get("skills_tag", {}).get("required_skills_tag", []),
                "preferredSkillsTag": job["parsed"].get("skills_tag", {}).get("preferred_skills_tag", [])
            }),
            "techStack": json.dumps(job["parsed"].get("tech_stack", [])),
            "languageRequirements": json.dumps(job["parsed"].get("language_requirements", [])),
            "benefits": json.dumps(job["parsed"].get("benefits", [])),
            "salaryRange": job["parsed"].get("salary_range"),
            "workArrangement": job["parsed"].get("work_arrangement"),
            "linkedinJobUrl": job["parsed"].get("linkedin_job_url"),
            "url": job["parsed"].get("url"),
            "minimumEducationLevel": job["parsed"].get("minimum_education_level"),
            "isExternal": False,
            "companyId": job["data"]["companyId"]
        }
        main_data.append(main_record)
        
        # Skill embeddings data (if exists)
        if "skillEmbeddings" in job["parsed"]:
            embeddings_data.append({
                "job_raw_id": job["job_raw_id"],
                "skillEmbeddings": json.dumps(job["parsed"]["skillEmbeddings"])
            })
    
    # Save to multiple formats for redundancy
    main_df = pd.DataFrame(main_data)
    embeddings_df = pd.DataFrame(embeddings_data) if embeddings_data else pd.DataFrame()
    
    # Save as CSV
    main_csv_path = BACKUP_DIR / f"enhanced_jobs_main_{batch_id}.csv"
    main_df.to_csv(main_csv_path, index=False)
    logger.info(f"💾 Saved main data to: {main_csv_path}")
    
    if not embeddings_df.empty:
        embeddings_csv_path = BACKUP_DIR / f"enhanced_jobs_embeddings_{batch_id}.csv"
        embeddings_df.to_csv(embeddings_csv_path, index=False)
        logger.info(f"💾 Saved embeddings data to: {embeddings_csv_path}")
    
    # Save raw processed data as pickle for exact recovery
    pickle_path = BACKUP_DIR / f"enhanced_jobs_raw_{batch_id}.pkl"
    with open(pickle_path, 'wb') as f:
        pickle.dump(processed_jobs, f)
    logger.info(f"💾 Saved raw data to: {pickle_path}")
    
    return {
        "batch_id": batch_id,
        "main_csv": main_csv_path,
        "embeddings_csv": embeddings_csv_path if not embeddings_df.empty else None,
        "pickle": pickle_path,
        "job_count": len(processed_jobs)
    }

def load_processed_data(batch_id: str) -> List[Dict]:
    """Load processed job data from backup files"""
    pickle_path = BACKUP_DIR / f"enhanced_jobs_raw_{batch_id}.pkl"
    
    if not pickle_path.exists():
        raise FileNotFoundError(f"Backup file not found: {pickle_path}")
    
    with open(pickle_path, 'rb') as f:
        processed_jobs = pickle.load(f)
    
    logger.info(f"📥 Loaded {len(processed_jobs)} jobs from backup: {pickle_path}")
    return processed_jobs

def populate_enhanced_jobs(mode: str = "full", batch_size: int = 100, batch_id: str = None, max_batches: int = None):
    """
    Main function with different modes:
    - 'full': Run both processing and ingestion phases
    - 'process': Run only processing phase (all jobs, paginated)
    - 'ingest': Run only ingestion phase (requires batch_id)
    - 'ingest-all': Ingest all available backup batches
    - 'list': List all available backup batches
    """
    try:
        if mode == "full":
            # Run both phases
            logger.info("🎯 Running FULL mode: Processing + Ingestion")
            batch_ids = process_jobs_phase(batch_size, max_batches)
            if batch_ids:
                successful_jobs, failed_jobs = ingest_jobs_phase(batch_ids=batch_ids)
                logger.info(f"🎉 FULL mode complete! Batches: {len(batch_ids)}, Success: {successful_jobs}, Failed: {failed_jobs}")
            else:
                logger.error("❌ Processing phase failed, skipping ingestion")
                
        elif mode == "process":
            # Run only processing phase (all jobs, paginated)
            logger.info("🧠 Running PROCESS mode: Paginated LLM Processing")
            if max_batches:
                logger.info(f"🔒 Limited to {max_batches} batches")
            batch_ids = process_jobs_phase(batch_size, max_batches)
            if batch_ids:
                logger.info(f"🎉 PROCESS mode complete! Created {len(batch_ids)} batches")
                logger.info(f"📋 Batch IDs: {batch_ids}")
                logger.info(f"📋 To ingest all batches, run: populate_enhanced_jobs(mode='ingest-all')")
                logger.info(f"📋 To ingest specific batch, run: populate_enhanced_jobs(mode='ingest', batch_id='{batch_ids[0]}')")
            else:
                logger.error("❌ Processing phase failed")
                
        elif mode == "ingest":
            # Run only ingestion phase
            if not batch_id:
                logger.error("❌ INGEST mode requires batch_id parameter")
                return
            logger.info(f"💾 Running INGEST mode: Database ingestion for batch {batch_id}")
            successful_jobs, failed_jobs = ingest_jobs_phase(batch_id=batch_id, from_backup=True)
            logger.info(f"🎉 INGEST mode complete! Success: {successful_jobs}, Failed: {failed_jobs}")
            
        elif mode == "ingest-all":
            # Ingest all available backup batches
            logger.info("💾 Running INGEST-ALL mode: Database ingestion for all backup batches")
            successful_jobs, failed_jobs = ingest_all_batches()
            logger.info(f"🎉 INGEST-ALL mode complete! Success: {successful_jobs}, Failed: {failed_jobs}")
            
        elif mode == "list":
            # List all available backup batches
            logger.info("📋 Running LIST mode: Show all backup batches")
            batch_info = list_available_batches()
            if batch_info:
                logger.info(f"📊 Found {len(batch_info)} backup batches")
                total_jobs = sum(info['job_count'] for info in batch_info if isinstance(info['job_count'], int))
                total_size = sum(info['size_mb'] for info in batch_info)
                logger.info(f"📊 Total jobs in backups: {total_jobs}")
                logger.info(f"📊 Total backup size: {total_size:.1f}MB")
            else:
                logger.info("📊 No backup batches found")
            
        else:
            logger.error(f"❌ Invalid mode: {mode}. Use 'full', 'process', 'ingest', 'ingest-all', or 'list'")
            
    except Exception as e:
        logger.error(f"❌ Fatal error: {str(e)}")
        raise

if __name__ == "__main__":
    import sys
    
    # Example usage with command line arguments
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        batch_size = int(sys.argv[2]) if len(sys.argv) > 2 else 100
        batch_id = sys.argv[3] if len(sys.argv) > 3 else None
        max_batches = int(sys.argv[4]) if len(sys.argv) > 4 else None
        
        populate_enhanced_jobs(mode=mode, batch_size=batch_size, batch_id=batch_id, max_batches=max_batches)
    else:
        # Default: run processing only (safer for bandwidth)
        print("🚀 Running in PROCESS mode (safer for bandwidth limits)")
        print("📝 Usage examples:")
        print("  python populate_enhanced_jobs.py process 50                    # Process all jobs in batches of 50")
        print("  python populate_enhanced_jobs.py process 100 20240603 2       # Process 2 batches of 100 jobs")
        print("  python populate_enhanced_jobs.py ingest <batch_id>             # Ingest specific batch")
        print("  python populate_enhanced_jobs.py ingest-all                    # Ingest all backup batches")
        print("  python populate_enhanced_jobs.py list                          # List all backup batches")
        print("  python populate_enhanced_jobs.py full 100                      # Process and ingest all jobs")
        print()
        print("🔍 First, let's see what backup batches exist:")
        list_available_batches()
        print()
        
        populate_enhanced_jobs(mode="process", batch_size=50, max_batches=2)  # Start with small test
