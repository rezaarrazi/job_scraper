import os
import openai
from google import genai
from sqlalchemy import create_engine, text
from cuid import cuid
import json
from typing import Optional, Dict, Any
from utils.logger import setup_logger
import logging
from dotenv import load_dotenv
from tqdm import tqdm
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.ai_client import AIClient

load_dotenv()

# --- CONFIG ---
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Setup logger
logger = setup_logger(__name__, level=logging.DEBUG)

COMPANY_DESCRIPTION_PROMPT = '''
You are an expert assistant tasked with cleaning and formatting company descriptions for professional summaries.

### Instructions:
Given a raw company description, generate a clean, well-structured, and professional summary that adheres to the following guidelines:

1. **Start with the company name**, followed by a concise overview of what they do.
2. The description should be **medium in length** — not too short, not too long (ideally 2–4 sentences).
3. Maintain a **professional tone** and write in clear, grammatically correct English.
4. Emphasize key information such as:
   - Mission or vision (if relevant)
   - Core business activities
   - Industry focus
   - Company size or scale
   - Key technologies or areas of specialization
   - Notable achievements (optional)
5. Remove any of the following:
   - Redundant phrases or filler content
   - Typos or grammatical errors
   - HTML tags or formatting characters
   - Contact information or job-related content
6. If the original description is very short, expand it with meaningful and accurate context. If it is too long, condense it while keeping essential details.

### Output Format:
Write a single, coherent paragraph that starts with the company name. For example:

> **Thales** is a global leader in advanced technologies, specialized in Defence & Security, Aeronautics & Space, and Cybersecurity & Digital identity. They are seeking a Software Engineer to contribute to the design of machine learning features and to enhance existing products, ensuring they meet customer requirements and quality goals.

> **Splunk** is a software company that provides operational intelligence software that monitors, reports, and analyzes real-time machine data. They are seeking an Applied Scientist to lead the development of AI/ML models and collaborate with cross-functional teams to integrate generative AI solutions into their products.

### Input:
{company_text}

Please provide a clean, professional description based on the input above.
'''

client = AIClient(provider='gemini', api_key=GEMINI_API_KEY)
engine = create_engine(DATABASE_URL)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((Exception,)),
    before_sleep=lambda retry_state: logger.warning(f"Retrying LLM call after error. Attempt {retry_state.attempt_number}/3")
)
def call_llm(company_text: str) -> Optional[str]:
    """Call LLM to enhance company description"""
    try:
        prompt = COMPANY_DESCRIPTION_PROMPT.format(company_text=company_text)
        
        response = client.generate_text(
            model='gemini-2.5-flash-preview-05-20',
            prompt=prompt,
            config={
                'max_output_tokens': 65535,
                'temperature': 0.2
            }
        )
        
        if response:
            return response.strip()
        else:
            error_msg = f"❌ LLM Error: {response}"
            logger.error(error_msg)
            raise Exception(error_msg)
    except Exception as e:
        logger.error(f"❌ LLM Error: {str(e)}")
        raise

def process_company_worker(row: Any) -> Optional[Dict[str, Any]]:
    """Process a single company's description"""
    try:
        company_id = row.id
        
        # Combine descriptions from both sources
        description = row.description or ""
        full_description = row.full_description or ""
        about = row.about or ""
        
        # Combine all descriptions with proper spacing
        combined_description = " ".join(filter(None, [
            description,
            full_description,
            about
        ])).strip()
        
        if not combined_description:
            logger.warning(f"⚠️ No description found for company ID: {company_id}")
            return None
            
        # Get enhanced description from LLM
        enhanced_description = call_llm(combined_description)
        if not enhanced_description:
            logger.warning(f"⚠️ Failed to enhance description for company ID: {company_id}")
            return None
            
        return {
            "company_id": company_id,
            "enhanced_description": enhanced_description
        }
    except Exception as e:
        logger.error(f"❌ Error processing company {company_id}: {str(e)}")
        return None

def populate_enhanced_company_descriptions():
    try:
        with engine.connect() as conn:
            # Get companies that haven't been processed yet
            rows = conn.execute(text("""
                SELECT 
                    c.id,
                    c.description,
                    c."fullDescription" as full_description,
                    csd.about
                FROM "Company" c
                LEFT JOIN "CompanyScrapingdog" csd ON c.id = csd."companyId"
                WHERE csd."enhancedDescription" IS NULL
            """)).fetchall()
            
            logger.debug(f"Found {len(rows)} companies to process")
            
            # Process companies concurrently
            max_workers = 50  # Number of concurrent workers
            successful_companies = 0
            failed_companies = 0
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all companies to the executor
                futures = {
                    executor.submit(process_company_worker, row): row for row in rows
                }
                
                # Create progress bar
                with tqdm(total=len(rows), desc="Processing companies", unit="company") as pbar:
                    for future in as_completed(futures):
                        row = futures[future]
                        try:
                            result = future.result()
                            if result:
                                # Update the company description
                                try:
                                    conn.execute(text("""
                                        UPDATE "CompanyScrapingdog"
                                        SET "enhancedDescription" = :enhanced_description,
                                            "updatedAt" = now()
                                        WHERE "companyId" = :company_id
                                    """), {
                                        "enhanced_description": result["enhanced_description"],
                                        "company_id": result["company_id"]
                                    })
                                    conn.commit()
                                    successful_companies += 1
                                except Exception as e:
                                    conn.rollback()
                                    logger.error(f"❌ Error updating company {row.id}: {str(e)}")
                                    failed_companies += 1
                            else:
                                failed_companies += 1
                        except Exception as e:
                            logger.error(f"❌ Error processing company {row.id}: {str(e)}")
                            failed_companies += 1
                        finally:
                            pbar.update(1)
                            pbar.set_postfix(
                                successful=successful_companies,
                                failed=failed_companies
                            )
            
            logger.info(f"✅ Processing complete. Successful: {successful_companies}, Failed: {failed_companies}")
            
    except Exception as e:
        logger.error(f"❌ Fatal error: {str(e)}")
        raise

if __name__ == "__main__":
    populate_enhanced_company_descriptions() 