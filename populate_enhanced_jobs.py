import os
import openai
from google import genai
import pandas as pd
from sqlalchemy import create_engine, text
from cuid import cuid
import json
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
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/postgres"
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")  # Replace with your actual key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Replace with your actual key

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


engine = create_engine(DATABASE_URL)

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
                    model='gemini-2.5-flash',
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
        response = openai_client.generate_embedding(text, model="text-embedding-3-small")
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
            
    return skill_embeddings

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

        # Process skill embeddings
        with engine.connect() as conn:
            required_skills = parsed.get("skills_tag", {}).get("required_skills_tag", [])
            preferred_skills = parsed.get("skills_tag", {}).get("preferred_skills_tag", [])
            
            # Generate embeddings for all skills
            required_skill_embeddings = process_skill_embeddings(conn, required_skills)
            preferred_skill_embeddings = process_skill_embeddings(conn, preferred_skills)
            
            # Add embeddings to the parsed data
            parsed['skillEmbeddings'] = {
                "requiredSkills": {skill: embedding for skill, embedding in required_skill_embeddings.items()},
                "preferredSkills": {skill: embedding for skill, embedding in preferred_skill_embeddings.items()}
            }
        
        return {
            "job_raw_id": job_raw_id,
            "data": data,
            "parsed": parsed
        }
    except Exception as e:
        logger.error(f"❌ Error processing job {row.jobTitle}: {str(e)}")
        return None

def populate_enhanced_jobs():
    try:
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

            # Get jobs that haven't been processed yet
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
            """)).fetchall()

            logger.debug(f"Found {len(rows)} jobs to process")

            # Process jobs concurrently
            max_workers = 10
            successful_jobs = 0
            failed_jobs = 0

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all jobs to the executor
                futures = {
                    executor.submit(
                        process_job_worker,
                        row,
                        industries_candidates,
                        job_titles,
                        engine
                    ): row for row in rows
                }

                # Create progress bar
                with tqdm(total=len(rows), desc="Processing jobs", unit="job") as pbar:
                    for future in as_completed(futures):
                        row = futures[future]
                        try:
                            result = future.result()
                            if result:
                                # Insert the processed job into the database
                                try:
                                    conn.execute(text("""
                                        INSERT INTO "EnhancedJobDetail" (
                                            "id", "jobTitle", "simplifiedJobTitle", "simplifiedJobTitleStandardized",
                                            "companyName", "companyLogoUrl", "companyIndustries", "companyType", "companyDescription", "contractType",
                                            "location", "experienceLevel", "minExperience", "maxExperience",
                                            "description", "responsibilities", "requiredProfile", "preferredProfile",
                                            "skillsTag", "skillEmbeddings", "techStack", "languageRequirements", "benefits", "salaryRange",
                                            "workArrangement", "linkedinJobUrl", "url", "minimumEducationLevel",
                                            "isExternal", "jobRawId", "companyId", "createdAt", "updatedAt"
                                        ) VALUES (
                                            COALESCE((SELECT "id" FROM "EnhancedJobDetail" WHERE "jobRawId" = :jobRawId), :id),
                                            :jobTitle, :simplifiedJobTitle, :simplifiedJobTitleStandardized,
                                            :companyName, :companyLogoUrl, :companyIndustries, :companyType, :companyDescription, :contractType,
                                            :location, :experienceLevel, :minExperience, :maxExperience,
                                            :description, :responsibilities, :requiredProfile, :preferredProfile,
                                            :skillsTag, :skillEmbeddings, :techStack, :languageRequirements, :benefits, :salaryRange,
                                            :workArrangement, :linkedinJobUrl, :url, :minimumEducationLevel,
                                            :isExternal, :jobRawId, :companyId,
                                            COALESCE((SELECT "createdAt" FROM "EnhancedJobDetail" WHERE "jobRawId" = :jobRawId), now()),
                                            now()
                                        )
                                        ON CONFLICT ("jobRawId") DO UPDATE SET
                                            "jobTitle" = EXCLUDED."jobTitle",
                                            "simplifiedJobTitle" = EXCLUDED."simplifiedJobTitle",
                                            "simplifiedJobTitleStandardized" = EXCLUDED."simplifiedJobTitleStandardized",
                                            "companyName" = EXCLUDED."companyName",
                                            "companyLogoUrl" = EXCLUDED."companyLogoUrl",
                                            "companyIndustries" = EXCLUDED."companyIndustries",
                                            "companyType" = EXCLUDED."companyType",
                                            "companyDescription" = EXCLUDED."companyDescription",
                                            "contractType" = EXCLUDED."contractType",
                                            "location" = EXCLUDED."location",
                                            "experienceLevel" = EXCLUDED."experienceLevel",
                                            "minExperience" = EXCLUDED."minExperience",
                                            "maxExperience" = EXCLUDED."maxExperience",
                                            "description" = EXCLUDED."description",
                                            "responsibilities" = EXCLUDED."responsibilities",
                                            "requiredProfile" = EXCLUDED."requiredProfile",
                                            "preferredProfile" = EXCLUDED."preferredProfile",
                                            "skillsTag" = EXCLUDED."skillsTag",
                                            "skillEmbeddings" = EXCLUDED."skillEmbeddings",
                                            "techStack" = EXCLUDED."techStack",
                                            "languageRequirements" = EXCLUDED."languageRequirements",
                                            "benefits" = EXCLUDED."benefits",
                                            "salaryRange" = EXCLUDED."salaryRange",
                                            "workArrangement" = EXCLUDED."workArrangement",
                                            "linkedinJobUrl" = EXCLUDED."linkedinJobUrl",
                                            "url" = EXCLUDED."url",
                                            "minimumEducationLevel" = EXCLUDED."minimumEducationLevel",
                                            "isExternal" = EXCLUDED."isExternal",
                                            "companyId" = EXCLUDED."companyId",
                                            "updatedAt" = now()
                                    """), {
                                        "id": cuid(),
                                        "jobTitle": result["parsed"].get("job_title"),
                                        "simplifiedJobTitle": result["parsed"].get("simplified_job_title"),
                                        "simplifiedJobTitleStandardized": result["parsed"].get("simplified_job_title_standardized"),
                                        "companyName": result["parsed"].get("company_name"),
                                        "companyLogoUrl": result["data"]["company_logo_url"],
                                        "companyIndustries": result["parsed"].get("company_industries") or [],
                                        "companyType": result["data"]["company_type"],
                                        "companyDescription": result["data"]["company_enhanced_description"],
                                        "contractType": result["parsed"].get("contract_type"),
                                        "location": result["parsed"].get("location"),
                                        "experienceLevel": result["parsed"].get("experience_level"),
                                        "minExperience": result["parsed"].get("min_experience"),
                                        "maxExperience": result["parsed"].get("max_experience"),
                                        "description": result["parsed"].get("description"),
                                        "responsibilities": result["parsed"].get("responsibilities") or [],
                                        "requiredProfile": result["parsed"].get("required_profile") or [],
                                        "preferredProfile": result["parsed"].get("preferred_profile") or [],
                                        "skillsTag": json.dumps({
                                            "requiredSkillsTag": result["parsed"].get("skills_tag", {}).get("required_skills_tag", []),
                                            "preferredSkillsTag": result["parsed"].get("skills_tag", {}).get("preferred_skills_tag", [])
                                        }),
                                        "skillEmbeddings": json.dumps(result["parsed"].get("skillEmbeddings", {})),
                                        "techStack": result["parsed"].get("tech_stack") or [],
                                        "languageRequirements": result["parsed"].get("language_requirements") or [],
                                        "benefits": result["parsed"].get("benefits") or [],
                                        "salaryRange": result["parsed"].get("salary_range"),
                                        "workArrangement": result["parsed"].get("work_arrangement"),
                                        "linkedinJobUrl": result["parsed"].get("linkedin_job_url"),
                                        "url": result["parsed"].get("url"),
                                        "minimumEducationLevel": result["parsed"].get("minimum_education_level"),
                                        "isExternal": False,
                                        "jobRawId": result["job_raw_id"],
                                        "companyId": result["data"]["companyId"]
                                    })
                                    conn.commit()
                                    successful_jobs += 1
                                except Exception as e:
                                    conn.rollback()
                                    logger.error(f"❌ Error inserting job {result['parsed'].get('job_title')}: {str(e)}")
                                    failed_jobs += 1
                            else:
                                failed_jobs += 1
                        except Exception as e:
                            logger.error(f"❌ Error processing job {row.jobTitle}: {str(e)}")
                            failed_jobs += 1
                        finally:
                            pbar.update(1)
                            pbar.set_postfix(
                                successful=successful_jobs,
                                failed=failed_jobs
                            )

            logger.info(f"✅ Processing complete. Successful: {successful_jobs}, Failed: {failed_jobs}")

    except Exception as e:
        logger.error(f"❌ Fatal error: {str(e)}")
        raise

if __name__ == "__main__":
    populate_enhanced_jobs()
