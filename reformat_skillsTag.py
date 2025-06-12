import os
import json
from dotenv import load_dotenv
from tqdm import tqdm
import logging
from utils.logger import setup_logger
from utils.ai_client import AIClient
from supabase import create_client, Client
from pydantic import BaseModel, Field
from typing import List, Dict, Any
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()

# Setup
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

logger = setup_logger(__name__, level=logging.INFO)

# Initialize clients
openai_client = AIClient(provider='openai', api_key=OPENAI_API_KEY)
gemini_client = AIClient(provider='gemini', api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Pydantic models for structured output
class PrioritizedSkill(BaseModel):
    skill: str = Field(description="The skill name")
    priority: float = Field(description="Priority score between 0.0 and 1.0", ge=0.0, le=1.0)

class SkillsPrioritization(BaseModel):
    skills: List[PrioritizedSkill] = Field(description="List of prioritized skills with scores")

def generate_embedding(text: str):
    """Generate embedding for given text using OpenAI."""
    try:
        response = openai_client.generate_embedding(text, model="text-embedding-ada-002")
        return response
    except Exception as e:
        logger.error(f"❌ Embedding error: {e}")
        return None

def query_similar_skills(embedding, similarity_threshold=0.8, top_k=5):
    """Query similar skills from Supabase using embedding similarity."""
    try:
        return supabase.rpc("match_skills", {
            "input_embedding": embedding,  # list of floats
            "similarity_threshold": similarity_threshold,
            "top_k": top_k
        }).execute()
    except Exception as e:
        logger.error(f"❌ Skill query error: {e}")
        return None

def prioritize_skills_with_llm(skills_list, job_context):
    """Use Gemini LLM to prioritize skills and assign priority scores."""
    if not skills_list:
        return []
    
    # Create a prompt for the LLM
    skills_text = ", ".join(skills_list)
    
    prompt = f"""
    You are a skill analysis expert. Given the following job context and candidate skills, please analyze and return the most relevant skills with priority scores (0.0 to 1.0).
    
    Job Context: {job_context[:1000]}...
    
    Candidate Skills Available: {skills_text}
    
    IMPORTANT CONSTRAINTS:
    - You MUST ONLY select skills from the provided candidate skills list above
    - DO NOT create, modify, or generate any new skill names
    - Use the EXACT skill names as provided in the candidate list
    - Select maximum 10 most relevant skills from the candidates
    
    Analyze each candidate skill's relevance to the job requirements and return only the most relevant ones with priority scores based on their importance for this specific role.
    
    Consider:
    - How directly the skill relates to the job responsibilities
    - Whether the skill is mentioned explicitly or implicitly in the job description
    - The importance of the skill for successful job performance
    - Industry standards and common requirements for this type of role
    
    Return only skills that exist in the candidate list with accurate priority scores.
    """
    
    try:
        response = gemini_client.generate_text(
            model='gemini-2.5-flash-preview-05-20',
            prompt=prompt,
            response_schema=SkillsPrioritization,
            config={
                'response_mime_type': 'application/json',
                'max_output_tokens': 8192,
                'temperature': 0.1
            },
            parse_response=True
        )
        
        # Convert Pydantic model to the expected list format
        if response and response.skills:
            validated_skills = []
            for skill_obj in response.skills:
                # Additional validation: ensure skill is in the original list
                if skill_obj.skill in skills_list:
                    validated_skills.append({
                        "skill": skill_obj.skill,
                        "priority": skill_obj.priority
                    })
                else:
                    logger.warning(f"LLM returned skill not in candidates: {skill_obj.skill}")
            return validated_skills
        
        return []
        
    except Exception as e:
        logger.error(f"❌ LLM prioritization error: {e}")
        return []

def process_job_skills(record: Dict[str, Any], job_title: str = ""):
    """Process job record to extract and prioritize skills from individual profile items."""
    # Extract profile items
    required_profile = []
    preferred_profile = []
    
    if record.get('requiredProfile'):
        try:
            if isinstance(record['requiredProfile'], str):
                required_profile = json.loads(record['requiredProfile'])
            else:
                required_profile = record['requiredProfile']
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Failed to parse requiredProfile for job: {job_title}")
            required_profile = []
    
    if record.get('preferredProfile'):
        try:
            if isinstance(record['preferredProfile'], str):
                preferred_profile = json.loads(record['preferredProfile'])
            else:
                preferred_profile = record['preferredProfile']
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Failed to parse preferredProfile for job: {job_title}")
            preferred_profile = []
    
    # Combine all profile items
    all_profile_items = required_profile + preferred_profile
    
    if not all_profile_items:
        logger.warning(f"No profile items found for job: {job_title}")
        return {"status": "no_data", "data": []}
    
    logger.debug(f"Processing {len(all_profile_items)} profile items for job: {job_title}")
    
    # Collect candidate skills from each profile item
    all_candidate_skills = set()  # Use set to automatically handle duplicates
    api_errors = 0
    total_items = 0
    
    for i, profile_item in enumerate(all_profile_items):
        if not profile_item or not str(profile_item).strip():
            continue
            
        total_items += 1
        logger.debug(f"Processing profile item {i+1}/{len(all_profile_items)}: {str(profile_item)[:50]}...")
        
        # Generate embedding for this specific profile item
        embedding = generate_embedding(str(profile_item))
        
        if not embedding:
            api_errors += 1
            logger.warning(f"Failed to generate embedding for profile item: {str(profile_item)[:50]}...")
            continue
        
        # Query similar skills for this profile item
        skill_results = query_similar_skills(embedding, similarity_threshold=0.75, top_k=5)
        
        if skill_results is None:
            # API error occurred
            api_errors += 1
            logger.error(f"API error querying skills for profile item: {str(profile_item)[:50]}...")
            continue
        elif skill_results and skill_results.data:
            # Extract skill names and add to our set
            item_skills = [item['text'] for item in skill_results.data if 'text' in item]
            all_candidate_skills.update(item_skills)
            logger.debug(f"Found {len(item_skills)} skills for profile item")
        else:
            logger.debug(f"No skills found for profile item: {str(profile_item)[:50]}...")
    
    # Check if we had too many API errors
    if api_errors > 0 and api_errors >= total_items * 0.5:  # More than 50% API errors
        logger.error(f"Too many API errors ({api_errors}/{total_items}) for job: {job_title}")
        return {"status": "api_error", "data": []}
    
    # Convert set back to list
    candidate_skills = list(all_candidate_skills)
    
    if not candidate_skills:
        if api_errors > 0:
            logger.warning(f"No candidate skills found for job: {job_title} (had {api_errors} API errors)")
            return {"status": "api_error", "data": []}
        else:
            logger.warning(f"No candidate skills found for job: {job_title}")
            return {"status": "no_skills", "data": []}
    
    logger.debug(f"Found {len(candidate_skills)} unique candidate skills for job: {job_title}")
    
    # Create job context for LLM from the record
    job_context_parts = []
    
    # Add job title as primary context
    if job_title:
        job_context_parts.append(f"Job Title: {job_title}")
    
    if record.get('description'):
        job_context_parts.append(f"Description: {record['description']}")
    if record.get('responsibilities'):
        try:
            responsibilities = json.loads(record['responsibilities']) if isinstance(record['responsibilities'], str) else record['responsibilities']
            if isinstance(responsibilities, list):
                job_context_parts.append(f"Responsibilities: {', '.join(responsibilities)}")
            else:
                job_context_parts.append(f"Responsibilities: {record['responsibilities']}")
        except:
            job_context_parts.append(f"Responsibilities: {record['responsibilities']}")
    if required_profile:
        job_context_parts.append(f"Required Profile: {', '.join(map(str, required_profile))}")
    if preferred_profile:
        job_context_parts.append(f"Preferred Profile: {', '.join(map(str, preferred_profile))}")
    
    job_context = " ".join(job_context_parts)
    
    # Use LLM to prioritize skills
    prioritized_skills = prioritize_skills_with_llm(candidate_skills, job_context)
    
    if not prioritized_skills:
        logger.error(f"LLM failed to prioritize skills for job: {job_title}")
        return {"status": "llm_error", "data": []}
    
    logger.debug(f"LLM prioritized {len(prioritized_skills)} skills for job: {job_title}")
    
    return {"status": "success", "data": prioritized_skills}

def get_total_records_count():
    """Get the total number of records to process using Supabase with proper pagination for counting."""
    try:
        # Use head() request to get count without data, and specify a large limit
        query = supabase.table("EnhancedJobDetail").select("id", count="exact", head=True)
        
        # Add WHERE conditions
        query = query.or_("description.not.is.null,responsibilities.not.is.null,requiredProfile.not.is.null,preferredProfile.not.is.null")
        query = query.or_("skillsTagNew.is.null,skillsTagNew.eq.[],skillsTagNew.eq.{}")
        
        result = query.execute()
        return result.count
    except Exception as e:
        logger.warning(f"⚠️ Could not get exact count, will use dynamic counting: {e}")
        return None  # Return None to indicate we should use dynamic counting

def get_batch_records(offset: int, batch_size: int):
    """Get a batch of records using Supabase with proper pagination handling."""
    try:
        query = supabase.table("EnhancedJobDetail").select(
            "id,description,responsibilities,requiredProfile,preferredProfile,jobTitle,skillsTagNew"
        )
        
        # Add WHERE conditions
        query = query.or_("description.not.is.null,responsibilities.not.is.null,requiredProfile.not.is.null,preferredProfile.not.is.null")
        query = query.or_("skillsTagNew.is.null,skillsTagNew.eq.[],skillsTagNew.eq.{}")
        
        # Add pagination - use limit and offset instead of range for better control
        query = query.order("id").limit(batch_size)
        
        # For offset > 0, we need to use range, but we need to be careful about the 1000 row limit
        if offset > 0:
            # Split large offsets into chunks to handle Supabase's 1000 row limit
            end_range = offset + batch_size - 1
            query = query.range(offset, end_range)
        
        result = query.execute()
        return result.data
    except Exception as e:
        logger.error(f"❌ Error fetching batch at offset {offset}: {e}")
        return []

def update_record_skills(record_id: str, skills_data: List[Dict]):
    """Update a single record with new skills using Supabase."""
    try:
        result = supabase.table("EnhancedJobDetail").update({
            "skillsTagNew": json.dumps(skills_data),
            "updatedAt": "now()"
        }).eq("id", record_id).execute()
        
        return True
    except Exception as e:
        logger.error(f"❌ Error updating record {record_id}: {e}")
        return False

def process_single_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single record and return the result."""
    try:
        record_id = record['id']
        job_title = record.get('jobTitle') or f"Job {record_id}"
        
        # Process skills for this job
        logger.debug(f"Processing skills for record {record_id} ({job_title})")
        result = process_job_skills(record, job_title)
        
        # Handle different result statuses
        if result["status"] == "api_error":
            logger.error(f"❌ API errors for record {record_id} ({job_title})")
            return {
                "status": "failed",
                "record_id": record_id,
                "job_title": job_title,
                "message": "API errors occurred"
            }
        elif result["status"] == "llm_error":
            logger.error(f"❌ LLM error for record {record_id} ({job_title})")
            return {
                "status": "failed",
                "record_id": record_id,
                "job_title": job_title,
                "message": "LLM processing failed"
            }
        elif result["status"] in ["no_data", "no_skills"]:
            logger.warning(f"No skills generated for record {record_id} ({job_title})")
            return {
                "status": "skipped",
                "record_id": record_id,
                "job_title": job_title,
                "message": "No skills generated"
            }
        elif result["status"] == "success":
            prioritized_skills = result["data"]
            
            # Update the record with new skills
            if update_record_skills(record_id, prioritized_skills):
                logger.info(f"✅ Updated record {record_id} ({job_title}) with {len(prioritized_skills)} skills")
                return {
                    "status": "success",
                    "record_id": record_id,
                    "job_title": job_title,
                    "skills_count": len(prioritized_skills)
                }
            else:
                logger.error(f"❌ Failed to update record {record_id}")
                return {
                    "status": "failed",
                    "record_id": record_id,
                    "job_title": job_title,
                    "message": "Database update failed"
                }
        else:
            logger.error(f"❌ Unknown result status for record {record_id}: {result['status']}")
            return {
                "status": "failed",
                "record_id": record_id,
                "job_title": job_title,
                "message": f"Unknown status: {result['status']}"
            }
        
    except Exception as e:
        record_id = record.get('id', 'unknown')
        logger.error(f"❌ Error processing record {record_id}: {str(e)}")
        return {
            "status": "failed",
            "record_id": record_id,
            "job_title": record.get('jobTitle', 'Unknown'),
            "message": str(e)
        }

def process_batch(offset: int, batch_size: int, max_workers: int = 3):
    """Process a batch of records using Supabase with parallel processing."""
    records = get_batch_records(offset, batch_size)
    
    if not records:
        return 0, 0, 0, 0
    
    batch_successful = 0
    batch_failed = 0
    batch_skipped = 0
    
    # Process records in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all records to the executor
        future_to_record = {
            executor.submit(process_single_record, record): record 
            for record in records
        }
        
        # Create a progress bar for this batch
        with tqdm(total=len(records), desc=f"🔄 Batch at offset {offset}", unit="job", leave=False) as batch_pbar:
            # Process completed futures
            for future in as_completed(future_to_record):
                record = future_to_record[future]
                try:
                    result = future.result()
                    
                    if result["status"] == "success":
                        batch_successful += 1
                    elif result["status"] == "skipped":
                        batch_skipped += 1
                    else:  # failed
                        batch_failed += 1
                        
                except Exception as e:
                    batch_failed += 1
                    record_id = record.get('id', 'unknown')
                    logger.error(f"❌ Future exception for record {record_id}: {str(e)}")
                finally:
                    batch_pbar.update(1)
                    batch_pbar.set_postfix(
                        success=batch_successful,
                        failed=batch_failed,
                        skipped=batch_skipped
                    )
    
    return batch_successful, batch_failed, batch_skipped, len(records)

def process_skills_tags(batch_size: int = 10, max_workers: int = 3):
    """
    Main function to process and generate skillsTagNew for EnhancedJobDetail table.
    Processes records in batches for memory efficiency using Supabase with proper pagination.
    """
    # Validate required environment variables
    if not all([OPENAI_API_KEY, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
        logger.error("❌ Missing required environment variables")
        logger.error("Required: OPENAI_API_KEY, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY")
        return
    
    try:
        # Try to get total count first
        logger.info("Counting total records to process...")
        total_records = get_total_records_count()
        
        if total_records is not None:
            logger.info(f"Found {total_records} records to process")
            if total_records == 0:
                logger.info("No records found to process")
                return
        else:
            logger.info("Using dynamic counting due to Supabase limitations...")
            total_records = None  # Will update dynamically
        
        successful_updates = 0
        failed_updates = 0
        skipped_updates = 0
        
        # Process records in batches with dynamic progress tracking
        if total_records:
            pbar = tqdm(total=total_records, desc="Processing skills tags", unit="record")
        else:
            pbar = tqdm(desc="Processing skills tags", unit="record")
        
        with pbar:
            offset = 0
            consecutive_empty_batches = 0
            max_empty_batches = 3  # Stop after 3 consecutive empty batches
            
            while True:
                try:
                    logger.debug(f"Processing batch: offset={offset}, batch_size={batch_size}")
                    
                    batch_successful, batch_failed, batch_skipped, batch_size_actual = process_batch(
                        offset, batch_size, max_workers
                    )
                    
                    # If we got no records, increment empty batch counter
                    if batch_size_actual == 0:
                        consecutive_empty_batches += 1
                        logger.debug(f"Empty batch {consecutive_empty_batches}/{max_empty_batches}")
                        if consecutive_empty_batches >= max_empty_batches:
                            logger.info("Reached end of data (multiple empty batches)")
                            break
                    else:
                        consecutive_empty_batches = 0  # Reset counter
                    
                    # Update counters
                    successful_updates += batch_successful
                    failed_updates += batch_failed
                    skipped_updates += batch_skipped
                    
                    # Update progress bar
                    if batch_size_actual > 0:
                        pbar.update(batch_size_actual)
                        pbar.set_postfix(
                            successful=successful_updates,
                            failed=failed_updates,
                            skipped=skipped_updates
                        )
                    
                    # If we got fewer records than batch_size, we might be done
                    if batch_size_actual < batch_size:
                        logger.info(f"Got {batch_size_actual} records (less than batch size {batch_size}), likely reached end")
                        break
                    
                    offset += batch_size
                    
                    # Brief pause between batches to respect API limits
                    time.sleep(1)
                        
                except Exception as e:
                    logger.error(f"❌ Error processing batch at offset {offset}: {str(e)}")
                    # Continue with next batch, but increment empty batch counter to avoid infinite loops
                    consecutive_empty_batches += 1
                    if consecutive_empty_batches >= max_empty_batches:
                        logger.error("Too many consecutive errors, stopping")
                        break
                    offset += batch_size
        
        total_processed = successful_updates + failed_updates + skipped_updates
        logger.info(f"""
        ✅ Skills processing complete!
        📊 Summary:
        - Successful updates: {successful_updates}
        - Failed updates: {failed_updates}
        - Skipped (no content/skills): {skipped_updates}
        - Total processed: {total_processed}
        - Total records found: {total_records if total_records else 'Unknown (dynamic counting)'}
        """)
        
    except Exception as e:
        logger.error(f"❌ Fatal error: {str(e)}")
        raise

if __name__ == "__main__":
    # You can adjust batch_size and max_workers based on your system's capacity and API rate limits
    # Smaller batch_size recommended due to API calls
    # max_workers controls parallel processing within each batch
    process_skills_tags(batch_size=30, max_workers=30)
