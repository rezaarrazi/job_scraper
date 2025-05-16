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
import logging
from dotenv import load_dotenv
from tqdm import tqdm
from datetime import datetime
from rapidfuzz import process, fuzz
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

load_dotenv()

# --- CONFIG ---
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/postgres"
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")  # Replace with your actual key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Replace with your actual key

# Setup logger
logger = setup_logger(__name__, level=logging.DEBUG)

# --- Schema Definition ---
class JobExtraction(BaseModel):
    job_title: str
    simplified_job_title: str
    simplified_job_title_standardized: str
    company_name: str
    company_logo_url: str
    company_industries: List[str]
    contract_type: str
    location: str
    experience_level: str
    min_experience: int
    max_experience: int
    description: str
    responsibilities: List[str]
    required_profile: List[str]
    preferred_profile: List[str]
    skills_tag: List[str]
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

5. **companyIndustries**
   * List of industries the company is in, use the industry mentioned in the job description or job metadata
   * The industry should be specific, not general. For example, "Technology" is not a specific industry, but "E-commerce" or "Fintech" is. And don't write short form of the industry, for example, "Tech", "AI" is not a specific industry, but "Information Technology" or "Artificial Intelligence" is.
   * If not stated, infer from job description, default to `["Other"]`
   * Example: `["E-commerce", "Fintech"]`

6. **contractType**
   * Strictly select from the following list:
        - `"Full-Time"` : for full-time or permanent positions
        - `"Part-Time"` : for part-time positions
        - `"Contract"` : for contract or temporary positions
        - `"Freelance"` : for freelance positions
        - `"Internship"` : for internship positions
        - `"Other"` : for other contract types
   * If not stated, infer based on context, default to `"Full-time"`

7. **location**
   * Format: `"City, State/Province, Country"`
   * If missing, infer from job or company context

8. **experienceLevel**
   * Reformulate the experience level from the job title and description, select from the following:
        - `"Entry-Level"`
        - `"Mid-Level"`  
        - `"Senior-Level"`  
        - `"Lead"`  
        - `"Director"`  
        - `"Executive"`
   * If not stated, infer from title and description, default to `"Entry-Level"`

9. **minExperience**
   * Integer: minimum required years of experience
   * If not stated, infer:
     * Entry: 0-2
     * Mid: 2-5
     * Senior: 5-10
     * Lead+: 8-15

10. **maxExperience**
    * Integer: maximum years of experience
    * Should be higher or equal to minExperience
    * Leave it empty if not stated

11. **description**
    * A detailed description of the job role, including its purpose, scope, and expectations.
    * Translate to English if needed
    * Highlight role purpose and expectations

12. **responsibilities**
    * A structured list outlining the key duties and responsibilities associated with this role.
    * Use action-oriented statements
    * If not explicitly listed, extract from job body

13. **requiredProfile**
    * List of essential qualifications or skills
    * Can include:
      * Technical skills (e.g., Java, SQL)
      * Soft skills (e.g., collaboration)
      * Education (e.g., Bachelor's in Computer Science)
      * Certifications (e.g., AWS Certified Developer)
    * Elaborate each item in the list
    * If not listed, infer based on job description, responsibilities and role type
    * Try to not leave it empty

14. **preferredProfile**
    * A list of additional, nice-to-have skills that would be beneficial but are not mandatory. 
    * Includes:
      * Advanced degrees
      * Extra certifications or tools
      * Domain knowledge (e.g., fintech, e-commerce)
    * Elaborate each item in the list
    * If not stated, infer from context
    * Try to not leave it empty

15. **skillsTag**
    * List of short, high-level tags summarizing the technical scope/skills and tech stacks/tools used in the job
    * Derived from:
      * jobTitle
      * requiredProfile and preferredProfile
      * industry context
    * Example: `["Backend", "Cloud", "DevOps", "React", "Node.js", "Python", "Docker", "Kubernetes", "AWS", "CI/CD", "Git", "SQL", "NoSQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "Kafka", "RabbitMQ", "Docker", "Kubernetes", "AWS", "CI/CD", "Git", "SQL", "NoSQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "Kafka", "RabbitMQ"]`

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

Make sure your result is in **valid JSON**, with all arrays using proper brackets `[]`, and strings wrapped in quotes.

Input:
{job_text}
'''

client = openai.OpenAI(api_key=OPENAI_API_KEY)

engine = create_engine(DATABASE_URL)

# --- LLM Wrapper ---
def call_llm(job_text):
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        prompt = JOB_EXTRACTION_PROMPT.format(job_text=job_text)
        
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[
                prompt,
            ],
            config={
                'response_mime_type': 'application/json',
                'response_schema': JobExtraction,
            }
        )
        
        if response.parsed:
            return response.parsed.model_dump(mode='json')
        else:
            print(f"❌ LLM Error: {response.text}")
            return None
    except Exception as e:
        print(f"❌ LLM Error: {e}")
        return None

# --- Embedding Helpers ---
def generate_embedding(text: str):
    try:
        response = client.embeddings.create(
            model="text-embedding-ada-002",
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"❌ Embedding error: {e}")
        return None

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

        # Process industries
        industries = []
        for industry in parsed['company_industries']:
            match, score, _ = process.extractOne(industry, industries_candidates, scorer=fuzz.token_set_ratio)
            if match and score > 80:
                industries.append(match)
            else:
                industries.append(industry)
        parsed['company_industries'] = industries

        # Process contract type
        contract_type_candidates = ["Full-Time", "Part-Time", "Contract", "Freelance", "Internship"]
        contract_type_match, contract_type_score, _ = process.extractOne(parsed['contract_type'], contract_type_candidates, scorer=fuzz.token_set_ratio)
        if contract_type_match and contract_type_score > 80:
            parsed['contract_type'] = contract_type_match
        else:
            parsed['contract_type'] = "Other"

        # Process experience level
        experience_level_candidates = ["Entry-Level", "Mid-Level", "Senior-Level", "Lead", "Director", "Executive"]
        experience_level_match, experience_level_score, _ = process.extractOne(parsed['experience_level'], experience_level_candidates, scorer=fuzz.token_set_ratio)
        if experience_level_match and experience_level_score > 80:
            parsed['experience_level'] = experience_level_match
        else:
            parsed['experience_level'] = "Entry-Level"
        
        # Process job title
        match_job_title, score_job_title, _ = process.extractOne(parsed["simplified_job_title"], job_titles, scorer=fuzz.token_set_ratio)
        if match_job_title and score_job_title > 80:
            parsed['simplified_job_title_standardized'] = match_job_title

        # Generate embeddings
        overall_text = "\n".join(filter(None, [
            f"Job Title: {parsed['job_title']}",
            f"\nDescription:\n{parsed['description'] or ''}",
            f"\nResponsibilities:" + "\n- ".join(parsed['responsibilities']) if parsed['responsibilities'] else "",
            f"\nRequired Profile:" + "\n- ".join(parsed['required_profile']) if parsed['required_profile'] else "",
            f"\nPreferred Profile:" + "\n- ".join(parsed['preferred_profile']) if parsed['preferred_profile'] else "",
        ]))
        embedding = generate_embedding(overall_text)
        embedding_json = json.dumps(embedding) if embedding else None

        return {
            "job_raw_id": job_raw_id,
            "data": data,
            "parsed": parsed,
            "embedding_json": embedding_json
        }
    except Exception as e:
        logger.error(f"❌ Error processing job {row.jobTitle}: {str(e)}")
        return None

def populate_enhanced_jobs():
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

    try:
        with engine.connect() as conn:
            # Get jobs that haven't been processed yet
            rows = conn.execute(text("""
                SELECT 
                    jr.*,
                    c."organizationName" as company_name,
                    csd."profilePhoto" as company_logo_url,
                    c."industries" as company_industry1,
                    csd."industry" as company_industry2,
                    csd."location" as company_location,
                    csd."companySize" as company_size,
                    csd."about" as company_about
                FROM "JobRaw" jr
                LEFT JOIN "Company" c ON jr."companyId" = c."id"
                LEFT JOIN "CompanyScrapingdog" csd ON c."id" = csd."companyId"
                LEFT JOIN "EnhancedJobDetail" ejd ON jr."id" = ejd."jobRawId"
                WHERE ejd."id" IS NULL
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
                                            "id", "companyId", "jobTitle", "simplifiedJobTitle", "simplifiedJobTitleStandardized", "companyName", "companyLogoUrl", "companyIndustries",
                                            "contractType", "location", "experienceLevel", "minExperience", "maxExperience",
                                            "description", "responsibilities", "requiredProfile", "preferredProfile",
                                            "skillsTag", "languageRequirements", "benefits", "salaryRange", "workArrangement",
                                            "linkedinJobUrl", "url", "isExternal", "minimumEducationLevel", "embedding",
                                            "jobRawId", "createdAt", "updatedAt"
                                        ) VALUES (
                                            COALESCE((SELECT "id" FROM "EnhancedJobDetail" WHERE "jobRawId" = :jobRawId), :id),
                                            :companyId, :jobTitle, :simplifiedJobTitle, :simplifiedJobTitleStandardized, :companyName, :companyLogoUrl, :companyIndustries,
                                            :contractType, :location, :experienceLevel, :minExperience, :maxExperience,
                                            :description, :responsibilities, :requiredProfile, :preferredProfile,
                                            :skillsTag, :languageRequirements, :benefits, :salaryRange, :workArrangement,
                                            :linkedinJobUrl, :url, :isExternal, :minimumEducationLevel, :embedding,
                                            :jobRawId,
                                            COALESCE((SELECT "createdAt" FROM "EnhancedJobDetail" WHERE "jobRawId" = :jobRawId), now()),
                                            now()
                                        )
                                        ON CONFLICT ("jobRawId") DO UPDATE SET
                                            "companyId" = EXCLUDED."companyId",
                                            "jobTitle" = EXCLUDED."jobTitle",
                                            "simplifiedJobTitle" = EXCLUDED."simplifiedJobTitle",
                                            "simplifiedJobTitleStandardized" = EXCLUDED."simplifiedJobTitleStandardized",
                                            "companyName" = EXCLUDED."companyName",
                                            "companyLogoUrl" = EXCLUDED."companyLogoUrl",
                                            "companyIndustries" = EXCLUDED."companyIndustries",
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
                                            "languageRequirements" = EXCLUDED."languageRequirements",
                                            "benefits" = EXCLUDED."benefits",
                                            "salaryRange" = EXCLUDED."salaryRange",
                                            "workArrangement" = EXCLUDED."workArrangement",
                                            "linkedinJobUrl" = EXCLUDED."linkedinJobUrl",
                                            "url" = EXCLUDED."url",
                                            "isExternal" = EXCLUDED."isExternal",
                                            "minimumEducationLevel" = EXCLUDED."minimumEducationLevel",
                                            "embedding" = EXCLUDED."embedding",
                                            "updatedAt" = now()
                                    """), {
                                        "id": cuid(),
                                        "companyId": result["data"]["companyId"],
                                        "jobTitle": result["parsed"].get("job_title"),
                                        "simplifiedJobTitle": result["parsed"].get("simplified_job_title"),
                                        "simplifiedJobTitleStandardized": result["parsed"].get("simplified_job_title_standardized"),
                                        "companyName": result["parsed"].get("company_name"),
                                        "companyLogoUrl": result["data"]["company_logo_url"],
                                        "companyIndustries": result["parsed"].get("company_industries") or [],
                                        "contractType": result["parsed"].get("contract_type"),
                                        "location": result["parsed"].get("location"),
                                        "experienceLevel": result["parsed"].get("experience_level"),
                                        "minExperience": result["parsed"].get("min_experience"),
                                        "maxExperience": result["parsed"].get("max_experience"),
                                        "description": result["parsed"].get("description"),
                                        "responsibilities": result["parsed"].get("responsibilities") or [],
                                        "requiredProfile": result["parsed"].get("required_profile") or [],
                                        "preferredProfile": result["parsed"].get("preferred_profile") or [],
                                        "skillsTag": result["parsed"].get("skills_tag") or [],
                                        "languageRequirements": result["parsed"].get("language_requirements") or [],
                                        "benefits": result["parsed"].get("benefits") or [],
                                        "salaryRange": result["parsed"].get("salary_range"),
                                        "workArrangement": result["parsed"].get("work_arrangement"),
                                        "linkedinJobUrl": result["parsed"].get("linkedin_job_url"),
                                        "url": result["parsed"].get("url"),
                                        "isExternal": False,
                                        "minimumEducationLevel": result["parsed"].get("minimum_education_level"),
                                        "embedding": result["embedding_json"],
                                        "jobRawId": result["job_raw_id"]
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
