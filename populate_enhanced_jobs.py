import os
import openai
from google import genai
import pandas as pd
from sqlalchemy import create_engine, text
from cuid import cuid
import json
from pydantic import BaseModel, Field
from typing import List, Optional
from utils.logger import setup_logger
import logging
from dotenv import load_dotenv
from tqdm import tqdm
from datetime import datetime
from rapidfuzz import process, fuzz

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
   * The official title of the position
   * Should be specific and match the company's terminology
   * Always translate to English
   * Example: `"Senior Software Engineer"`

2. **simplifiedJobTitle**
    * Translate the job title to English.
    * Format in **Title Case** (e.g., "Product Manager"), but keep known abbreviations (e.g., CTO, CRM) in **UPPERCASE**.
    * Remove any mention of:
        - Seniority (e.g., "Senior", "Junior")
        - Gender
        - Age
        - Location
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
    * List of short, high-level tags summarizing the technical scope
    * Derived from:
      * jobTitle
      * requiredProfile and preferredProfile
      * industry context
    * Example: `["Backend", "Cloud", "DevOps"]`

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

# --- Main Logic ---
def populate_enhanced_jobs():
    industries = pd.read_csv(os.path.join(os.getcwd(), 'data/output/industries/unique_industries.csv'))['industry'].tolist()
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

            pbar = tqdm(rows, desc="Processing jobs")
            for row in pbar:
                job_raw_id = row.id
                pbar.set_description(f"🔍 Processing {row.jobTitle}")

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
                    continue

                industries = []
                for industry in parsed['companyIndustries']:
                    match, score, _ = process.extractOne(industry, industries, scorer=fuzz.token_set_ratio)
                    if match and score > 70:
                        industries.append(match)
                    else:
                        industries.append(industry)
                parsed['companyIndustries'] = industries

                contract_type_candidates = ["Full-Time", "Part-Time", "Contract", "Freelance", "Internship"]
                contract_type_match, contract_type_score, _ = process.extractOne(parsed['contractType'], contract_type_candidates, scorer=fuzz.token_set_ratio)
                if contract_type_match and contract_type_score > 70:
                    parsed['contractType'] = contract_type_match
                else:
                    parsed['contractType'] = "Other"

                experience_level_candidates = ["Entry-Level", "Mid-Level", "Senior-Level", "Lead", "Director", "Executive"]
                experience_level_match, experience_level_score, _ = process.extractOne(parsed['experienceLevel'], experience_level_candidates, scorer=fuzz.token_set_ratio)
                if experience_level_match and experience_level_score > 70:
                    parsed['experienceLevel'] = experience_level_match
                else:
                    parsed['experienceLevel'] = "Entry-Level"
                
                match_job_title, score_job_title, _ = process.extractOne(parsed["simplifiedJobTitle"], job_titles, scorer=fuzz.token_set_ratio)
                if match_job_title and score_job_title > 70:
                    parsed['simplifiedJobTitle'] = match_job_title

                try:
                    # Build fields
                    overall_text = "\n".join(filter(None, [
                        f"Job Title: {parsed['job_title']}",
                        f"\nDescription:\n{parsed['description'] or ''}",
                        f"\nResponsibilities:" + "\n- ".join(parsed['responsibilities']) if parsed['responsibilities'] else "",
                        f"\nRequired Profile:" + "\n- ".join(parsed['required_profile']) if parsed['required_profile'] else "",
                        f"\nPreferred Profile:" + "\n- ".join(parsed['preferred_profile']) if parsed['preferred_profile'] else "",
                    ]))
                    # Generate embeddings
                    embedding = generate_embedding(overall_text)
                    
                    # Convert embeddings to JSON
                    embedding_json = json.dumps(embedding) if embedding else None

                    # Upsert the enhanced job
                    result = conn.execute(text("""
                        INSERT INTO "EnhancedJobDetail" (
                            "id", "jobTitle", "companyName", "companyLogoUrl", "companyIndustries",
                            "contractType", "location", "experienceLevel", "minExperience", "maxExperience",
                            "description", "responsibilities", "requiredProfile", "preferredProfile",
                            "skillsTag", "languageRequirements", "benefits", "salaryRange", "workArrangement",
                            "linkedinJobUrl", "url", "isExternal", "minimumEducationLevel", "embedding",
                            "jobRawId", "createdAt", "updatedAt"
                        ) VALUES (
                            COALESCE((SELECT "id" FROM "EnhancedJobDetail" WHERE "jobRawId" = :jobRawId), :id),
                            :jobTitle, :companyName, :companyLogoUrl, :companyIndustries,
                            :contractType, :location, :experienceLevel, :minExperience, :maxExperience,
                            :description, :responsibilities, :requiredProfile, :preferredProfile,
                            :skillsTag, :languageRequirements, :benefits, :salaryRange, :workArrangement,
                            :linkedinJobUrl, :url, :isExternal, :minimumEducationLevel, :embedding,
                            :jobRawId,
                            COALESCE((SELECT "createdAt" FROM "EnhancedJobDetail" WHERE "jobRawId" = :jobRawId), now()),
                            now()
                        )
                        ON CONFLICT ("jobRawId") DO UPDATE SET
                            "jobTitle" = EXCLUDED."jobTitle",
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
                        "jobTitle": parsed.get("job_title"),
                        "companyName": parsed.get("company_name"),
                        "companyLogoUrl": parsed.get("company_logo_url"),
                        "companyIndustries": parsed.get("company_industries") or [],
                        "contractType": parsed.get("contract_type"),
                        "location": parsed.get("location"),
                        "experienceLevel": parsed.get("experience_level"),
                        "minExperience": parsed.get("min_experience"),
                        "maxExperience": parsed.get("max_experience"),
                        "description": parsed.get("description"),
                        "responsibilities": parsed.get("responsibilities") or [],
                        "requiredProfile": parsed.get("required_profile") or [],
                        "preferredProfile": parsed.get("preferred_profile") or [],
                        "skillsTag": parsed.get("skills_tag") or [],
                        "languageRequirements": parsed.get("language_requirements") or [],
                        "benefits": parsed.get("benefits") or [],
                        "salaryRange": parsed.get("salary_range"),
                        "workArrangement": parsed.get("work_arrangement"),
                        "linkedinJobUrl": parsed.get("linkedin_job_url"),
                        "url": parsed.get("url"),
                        "isExternal": parsed.get("is_external", False),
                        "minimumEducationLevel": parsed.get("minimum_education_level"),
                        "embedding": embedding_json,
                        "jobRawId": job_raw_id
                    })

                    # Commit the transaction
                    conn.commit()

                    # Verify the inserted data
                    inserted_data = conn.execute(text("""
                        SELECT 
                            "id", "jobTitle", "companyName", "location", 
                            "experienceLevel", "contractType", "createdAt"
                        FROM "EnhancedJobDetail"
                        WHERE "jobRawId" = :job_raw_id
                    """), {"job_raw_id": job_raw_id}).fetchone()
                    
                    if inserted_data:
                        # Also check the total count
                        total_count = conn.execute(text("""
                            SELECT COUNT(*) FROM "EnhancedJobDetail"
                        """)).scalar()
                        pbar.set_description(f"✅ Enhanced: {parsed['job_title']} - {total_count} records")
                    else:
                        logger.warning(f"⚠️ Could not find inserted data for job: {parsed['job_title']}")

                except Exception as e:
                    conn.rollback()
                    logger.error(f"❌ Error inserting job {parsed.get('job_title')}: {str(e)}")
                    continue

    except Exception as e:
        logger.error(f"❌ Fatal error: {str(e)}")
        raise

if __name__ == "__main__":
    populate_enhanced_jobs()
