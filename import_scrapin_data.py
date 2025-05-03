import requests
import openai
from sqlalchemy import create_engine, text
import psycopg2
from cuid import cuid
import time
import os
from dotenv import load_dotenv
from tqdm import tqdm
import json
from utils.logger import setup_logger

# Load environment variables from .env file
load_dotenv()

# --- CONFIG ---
API_KEY = os.getenv("SCRAPIN_API_KEY")
SCRAPIN_URL = "https://api.scrapin.io/enrichment/company"
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

engine = create_engine(DATABASE_URL, echo=False)

import logging
logger = setup_logger(__name__, level=logging.WARNING)

def get_company_data(linkedin_url):
    params = {"apikey": API_KEY, "linkedinUrl": linkedin_url}
    try:
        r = requests.get(SCRAPIN_URL, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"❌ ScrapIn error for {linkedin_url}: {e}")
        return None

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

def import_scrapin_data():
    try:
        # Create a new engine with echo=True to see SQL statements
        engine = create_engine(DATABASE_URL, echo=False)
        
        with engine.connect() as conn:
            # Get companies that haven't been processed yet
            companies = conn.execute(text("""
                SELECT c.id, c."linkedin", c."organizationName"
                FROM "Company" c
                LEFT JOIN "CompanyScrapin" s ON s."companyId" = c.id
                WHERE c."linkedin" IS NOT NULL AND s."id" IS NULL
            """)).fetchall()

            logger.info(f"Found {len(companies)} companies to process")

            pbar = tqdm(companies, desc="Processing companies")
            for company_id, linkedin, name in pbar:
                pbar.set_description(f"🔍 Processing {name}")
                data = get_company_data(linkedin)
                if not data or not data.get("success") or not data.get("company"):
                    logger.warning(f"⚠️ Skipping {name} due to invalid data")
                    continue

                comp = data["company"]
                embed_text = " ".join(filter(None, [
                    comp.get("tagline"),
                    comp.get("industry"),
                    ", ".join(comp.get("specialities", []))
                ]))
                embedding = generate_embedding(embed_text)
                
                # Convert embedding to JSON string
                embedding_json = json.dumps(embedding) if embedding else None

                try:
                    result = conn.execute(text("""
                        INSERT INTO "CompanyScrapin" (
                            "id", "companyId", "linkedInId", "name", "universalName", "linkedInUrl",
                            "employeeCount", "followerCount", "employeeCountRangeStart", "employeeCountRangeEnd",
                            "websiteUrl", "tagline", "description", "industry", "phone", "specialities",
                            "headquarterCity", "headquarterCountry", "headquarterPostalCode", "headquarterGeographicArea",
                            "headquarterStreet1", "headquarterStreet2", "logoUrl", "foundedYear",
                            "fundingNumberOfRounds", "lastFundingType", "lastFundingAmount", "lastFundingCurrency",
                            "lastFundingRoundUrl", "lastFundingAnnouncedOn", "leadInvestors", "backgroundUrl",
                            "embedding", "createdAt"
                        ) VALUES (
                            :id, :companyId, :linkedInId, :name, :universalName, :linkedInUrl,
                            :employeeCount, :followerCount, :employeeCountRangeStart, :employeeCountRangeEnd,
                            :websiteUrl, :tagline, :description, :industry, :phone, :specialities,
                            :headquarterCity, :headquarterCountry, :headquarterPostalCode, :headquarterGeographicArea,
                            :headquarterStreet1, :headquarterStreet2, :logoUrl, :foundedYear,
                            :fundingNumberOfRounds, :lastFundingType, :lastFundingAmount, :lastFundingCurrency,
                            :lastFundingRoundUrl, :lastFundingAnnouncedOn, :leadInvestors, :backgroundUrl,
                            :embedding, now()
                        )
                    """), {
                        "id": cuid(),
                        "companyId": company_id,
                        "linkedInId": comp.get("linkedInId"),
                        "name": comp.get("name"),
                        "universalName": comp.get("universalName"),
                        "linkedInUrl": comp.get("linkedInUrl"),
                        "employeeCount": comp.get("employeeCount"),
                        "followerCount": comp.get("followerCount"),
                        "employeeCountRangeStart": comp.get("employeeCountRange", {}).get("start"),
                        "employeeCountRangeEnd": comp.get("employeeCountRange", {}).get("end"),
                        "websiteUrl": comp.get("websiteUrl"),
                        "tagline": comp.get("tagline"),
                        "description": comp.get("description"),
                        "industry": comp.get("industry"),
                        "phone": comp.get("phone"),
                        "specialities": comp.get("specialities", []),
                        "headquarterCity": comp.get("headquarter", {}).get("city"),
                        "headquarterCountry": comp.get("headquarter", {}).get("country"),
                        "headquarterPostalCode": comp.get("headquarter", {}).get("postalCode"),
                        "headquarterGeographicArea": comp.get("headquarter", {}).get("geographicArea"),
                        "headquarterStreet1": comp.get("headquarter", {}).get("street1"),
                        "headquarterStreet2": comp.get("headquarter", {}).get("street2"),
                        "logoUrl": comp.get("logo"),
                        "foundedYear": comp.get("foundedOn", {}).get("year"),
                        "fundingNumberOfRounds": (comp.get("fundingData") or {}).get("numberOfFundingRounds"),
                        "lastFundingType": ((comp.get("fundingData") or {}).get("lastFundingRound") or {}).get("fundingType"),
                        "lastFundingAmount": (((comp.get("fundingData") or {}).get("lastFundingRound") or {}).get("moneyRaised") or {}).get("amount"),
                        "lastFundingCurrency": (((comp.get("fundingData") or {}).get("lastFundingRound") or {}).get("moneyRaised") or {}).get("currencyCode"),
                        "lastFundingRoundUrl": ((comp.get("fundingData") or {}).get("lastFundingRound") or {}).get("fundingRoundUrl"),
                        "lastFundingAnnouncedOn": ((comp.get("fundingData") or {}).get("lastFundingRound") or {}).get("announcedOn"),
                        "leadInvestors": [i["name"] for i in (((comp.get("fundingData") or {}).get("lastFundingRound") or {}).get("leadInvestors") or [])],
                        "backgroundUrl": comp.get("backgroundUrl"),
                        "embedding": embedding_json,
                    })
                    conn.commit()

                    # Verify the inserted data
                    inserted_data = conn.execute(text("""
                        SELECT "id", "name", "linkedInId", "employeeCount", "followerCount", "createdAt"
                        FROM "CompanyScrapin"
                        WHERE "companyId" = :company_id
                    """), {"company_id": company_id}).fetchone()
                    
                    if inserted_data:
                        # Also check the total count
                        total_count = conn.execute(text("""
                            SELECT COUNT(*) FROM "CompanyScrapin"
                        """)).scalar()
                        pbar.set_description(f"🔍 Processing {name} - Successfully inserted - {total_count} records")
                    else:
                        logger.warning(f"⚠️ Could not find inserted data for {name}")

                except Exception as e:
                    conn.rollback()
                    logger.error(f"❌ Error inserting {name}: {str(e)}")
                    raise

                time.sleep(1)
    except Exception as e:
        logger.error(f"❌ Fatal error: {str(e)}")
        raise

if __name__ == "__main__":
    import_scrapin_data()
