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

load_dotenv()

# --- CONFIG ---
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/postgres"
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")  # Replace with your actual key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Replace with your actual key

# Setup logger
logger = setup_logger(__name__, level=logging.DEBUG)

# --- Schema Definition ---
class JobExtraction(BaseModel):
    job_title: str = Field(..., description="Title of the job position")
    job_type: Optional[str] = Field(None, description="Type of employment (Full-time, Part-time, etc.)")
    location: Optional[str] = Field(None, description="Job location")
    experience_level: Optional[str] = Field(None, description="Experience level required")
    min_experience: Optional[int] = Field(None, description="Minimum years of experience required")
    max_experience: Optional[int] = Field(None, description="Maximum years of experience required")
    description: str = Field(..., description="Complete job description")
    responsibilities: List[str] = Field(default_factory=list, description="List of job responsibilities")
    required_skills: List[str] = Field(default_factory=list, description="List of required skills")
    preferred_skills: List[str] = Field(default_factory=list, description="List of preferred skills")
    skills_tag: List[str] = Field(default_factory=list, description="List of skill tags")
    language_requirements: List[str] = Field(default_factory=list, description="List of language requirements")

JOB_EXTRACTION_PROMPT = '''
You are an expert assistant tasked with extracting structured data from job postings.

Given a full job description, extract and format the following fields clearly and concisely. Try to fill in all fields with reasonable inferences when explicit information is not available. Avoid leaving fields empty unless absolutely necessary.

1. **Job Title**
   - The official title of the position
   - Should be specific and match the company's terminology
   - Always translate the job title to English
   - Example: "Senior Software Engineer" or "Product Manager"

2. **Job Type**
   - The employment arrangement type
   - Common values: Full-time, Part-time, Contract, Internship
   - If not specified, infer from context (e.g., if it's a permanent role, assume Full-time)
   - Example: "Full-time" or "Contract"

3. **Location**
   - Where the job is located
   - Format: "City, State/Province, Country"
   - If any part is missing, use available parts
   - If not specified, infer from company location or job context
   - Examples: 
     * "San Francisco, CA, United States"
     * "Jakarta, DKI Jakarta, Indonesia"
     * "Remote, United States"

4. **Experience Level**
   - The seniority level required for the position
   - Common values: Entry, Mid, Senior, Lead, Principal
   - If not specified, infer from job title and requirements
   - Example: "Senior" or "Entry Level"

5. **Minimum Experience**
   - The minimum number of years of experience required
   - Should be a whole number
   - If not explicitly mentioned, infer from experience level:
     * Entry/Junior: 0-2 years
     * Mid/Intermediate: 2-5 years
     * Senior: 5-10 years
     * Lead/Principal: 8-15 years
   - Example: 2 or 5

6. **Maximum Experience**
   - The maximum number of years of experience required
   - Should be a whole number
   - If not explicitly mentioned, infer from experience level using the ranges above
   - Example: 5 or 10

7. **Description**
   - A comprehensive overview of the job
   - Should include the main purpose and scope of the role
   - Keep it concise but informative
   - If not provided, create a summary based on the job title and requirements
   - Always translate the description to English

8. **Responsibilities**
   - List of key duties and responsibilities
   - Each item should be a clear, actionable statement
   - If not explicitly listed, infer from the job description by identifying key duties and tasks
   - Example: ["Lead development team", "Design system architecture"]

9. **Required Skills**
   - Essential skills and qualifications
   - Technical skills, tools, and technologies
   - If not explicitly listed, infer from the job description by identifying:
     * Technical requirements mentioned
     * Tools and technologies referenced
     * Required qualifications
   - Example: ["Python", "AWS", "Docker"]

10. **Preferred Skills**
    - Desirable but not mandatory skills
    - Additional qualifications that would be beneficial
    - If not explicitly listed, infer from the job description by identifying:
      * Nice-to-have technologies
      * Additional qualifications mentioned
      * Related skills that would be beneficial
    - Example: ["Kubernetes", "Machine Learning"]

11. **Skills Tag**
    - Keywords and tags related to the role
    - Used for categorization and search
    - If not explicitly listed, infer from:
      * Job title
      * Required and preferred skills
      * Industry and domain knowledge mentioned
    - Example: ["Backend", "Cloud", "DevOps"]

12. **Language Requirements**
    - Required language proficiencies
    - Include level if specified (e.g., "Fluent", "Native")
    - If not explicitly mentioned:
      * Check the language of the job description
      * Infer from the job location and company context
      * Consider common language requirements for the role
      * Most likely the default required language is Bahasa Indonesia except for the job location is outside Indonesia
    - Example: ["Bahasa Indonesia", "English", "Spanish"]

Format the output as a JSON object. All list-based fields must be proper JSON arrays. Try to fill in all fields with reasonable inferences when explicit information is not available. Only use `null` or empty lists as a last resort when no reasonable inference can be made.
'''

client = openai.OpenAI(api_key=OPENAI_API_KEY)

engine = create_engine(DATABASE_URL)

# --- LLM Wrapper ---
def call_llm(job_text):
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[
                JOB_EXTRACTION_PROMPT,
                job_text
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
    try:
        with engine.connect() as conn:
            # Get jobs that haven't been processed yet
            rows = conn.execute(text("""
                SELECT jr.* FROM "JobRaw" jr
            """)).fetchall()

            logger.debug(f"Found {len(rows)} jobs to process")

            pbar = tqdm(rows, desc="Processing jobs")
            for row in pbar:
                job_raw_id = row.id
                pbar.set_description(f"🔍 Processing {row.jobTitle}")

                text_to_send = "\n".join(filter(None, [
                    f"Job Title: {row.jobTitle}",
                    f"Company: {row.companyName}",
                    f"Location: {row.location}",
                    f"Work Arrangement: {row.workArrangement}",
                    f"Contract Type: {row.contractType}",
                    f"Seniority Level: {row.seniorityLevel}",
                    f"\nDescription:\n{row.description or ''}"
                ]))

                parsed = call_llm(text_to_send)
                if not parsed:
                    logger.warning(f"⚠️ Skipped due to LLM error for job: {row.jobTitle}")
                    continue

                try:
                    # Build fields
                    skill_text = ", ".join((parsed.get("required_skills") or []) + (parsed.get("preferred_skills") or []) + (parsed.get("skills_tag") or []))
                    overall_text = " ".join(filter(None, [
                        parsed.get("job_title"),
                        parsed.get("location"),
                        parsed.get("experience_level"),
                        parsed.get("description"),
                        "\n".join(parsed.get("responsibilities") or [])
                    ]))

                    # Generate embeddings
                    embedding = generate_embedding(overall_text)
                    skills_embedding = generate_embedding(skill_text)

                    # Convert embeddings to JSON
                    embedding_json = json.dumps(embedding) if embedding else None
                    skills_embedding_json = json.dumps(skills_embedding) if skills_embedding else None

                    # Upsert the enhanced job
                    result = conn.execute(text("""
                        INSERT INTO "EnhancedJobDetail" (
                            "id", "jobTitle", "jobType", "location", "experienceLevel",
                            "minExperience", "maxExperience", "description",
                            "responsibilities", "requiredSkills", "preferredSkills", "skillsTag",
                            "url", "isExternal", "jobRawId",
                            "embedding", "skills_embedding", "languageRequirements",
                            "createdAt", "updatedAt"
                        ) VALUES (
                            COALESCE((SELECT "id" FROM "EnhancedJobDetail" WHERE "jobRawId" = :jobRawId), :id),
                            :jobTitle, :jobType, :location, :experienceLevel,
                            :minExperience, :maxExperience, :description,
                            :responsibilities, :requiredSkills, :preferredSkills, :skillsTag,
                            :url, :isExternal, :jobRawId,
                            :embedding, :skills_embedding, :languageRequirements,
                            COALESCE((SELECT "createdAt" FROM "EnhancedJobDetail" WHERE "jobRawId" = :jobRawId), now()),
                            now()
                        )
                        ON CONFLICT ("jobRawId") DO UPDATE SET
                            "jobTitle" = EXCLUDED."jobTitle",
                            "jobType" = EXCLUDED."jobType",
                            "location" = EXCLUDED."location",
                            "experienceLevel" = EXCLUDED."experienceLevel",
                            "minExperience" = EXCLUDED."minExperience",
                            "maxExperience" = EXCLUDED."maxExperience",
                            "description" = EXCLUDED."description",
                            "responsibilities" = EXCLUDED."responsibilities",
                            "requiredSkills" = EXCLUDED."requiredSkills",
                            "preferredSkills" = EXCLUDED."preferredSkills",
                            "skillsTag" = EXCLUDED."skillsTag",
                            "url" = EXCLUDED."url",
                            "isExternal" = EXCLUDED."isExternal",
                            "embedding" = EXCLUDED."embedding",
                            "skills_embedding" = EXCLUDED."skills_embedding",
                            "languageRequirements" = EXCLUDED."languageRequirements",
                            "updatedAt" = now()
                    """), {
                        "id": cuid(),
                        "jobTitle": parsed.get("job_title"),
                        "jobType": parsed.get("job_type"),
                        "location": parsed.get("location"),
                        "experienceLevel": parsed.get("experience_level"),
                        "minExperience": parsed.get("min_experience"),
                        "maxExperience": parsed.get("max_experience"),
                        "description": parsed.get("description"),
                        "responsibilities": parsed.get("responsibilities") or [],
                        "requiredSkills": parsed.get("required_skills") or [],
                        "preferredSkills": parsed.get("preferred_skills") or [],
                        "skillsTag": parsed.get("skills_tag") or [],
                        "url": row.linkedinJobUrl,
                        "isExternal": False,
                        "jobRawId": job_raw_id,
                        "embedding": embedding_json,
                        "skills_embedding": skills_embedding_json,
                        "languageRequirements": parsed.get("language_requirements") or []
                    })

                    # Commit the transaction
                    conn.commit()

                    # Verify the inserted data
                    inserted_data = conn.execute(text("""
                        SELECT "id", "jobTitle", "jobType", "location", "createdAt"
                        FROM "EnhancedJobDetail"
                        WHERE "jobRawId" = :job_raw_id
                    """), {"job_raw_id": job_raw_id}).fetchone()
                    
                    if inserted_data:
                        # Also check the total count
                        total_count = conn.execute(text("""
                            SELECT COUNT(*) FROM "EnhancedJobDetail"
                        """)).scalar()
                        pbar.set_description(f"✅ Enhanced: {parsed.get('job_title')} - {total_count} records")
                    else:
                        logger.warning(f"⚠️ Could not find inserted data for job: {parsed.get('job_title')}")

                except Exception as e:
                    conn.rollback()
                    logger.error(f"❌ Error inserting job {parsed.get('job_title')}: {str(e)}")
                    continue

    except Exception as e:
        logger.error(f"❌ Fatal error: {str(e)}")
        raise

if __name__ == "__main__":
    populate_enhanced_jobs()
