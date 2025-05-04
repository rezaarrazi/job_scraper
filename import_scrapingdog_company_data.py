import requests
import json
import time
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from cuid import cuid
from tqdm import tqdm
from utils.logger import setup_logger

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
SCRAPINGDOG_API_KEY = os.getenv("SCRAPINGDOG_API_KEY")
SCRAPINGDOG_URL = "https://api.scrapingdog.com/linkedin"

logger = setup_logger(__name__)
engine = create_engine(DATABASE_URL)

def get_scrapingdog_data(linkedinId):
    params = {
        "api_key": SCRAPINGDOG_API_KEY,
        "type": "company",
        "linkId": linkedinId,
        "private": "false"
    }
    try:
        response = requests.get(SCRAPINGDOG_URL, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"❌ Scrapingdog error for {linkedinId}: {e}")
        return None

def extract_field(data, field, default=None):
    return data.get(field, default)

def import_scrapingdog_data():
    with engine.connect() as conn:
        companies = conn.execute(text("""
            SELECT c.id, c."linkedin", c."organizationName"
            FROM "Company" c
            LEFT JOIN "CompanyScrapingdog" s ON s."companyId" = c.id
            WHERE c."linkedin" IS NOT NULL AND s."id" IS NULL
        """)).fetchall()

        logger.info(f"Found {len(companies)} companies to scrape")
        pbar = tqdm(companies, desc="🔍 Scraping LinkedIn")

        for company_id, linkedin, name in pbar:
            pbar.set_description(f"🔍 {name}")
            
            linkedinId = linkedin.strip("/").split("/")[-1]
            data_list = get_scrapingdog_data(linkedinId)
            if (not data_list) or len(data_list) == 0:
                logger.warning(f"⚠️ Skipping {name}, failed to fetch data")
                continue

            data = data_list[0]

            try:
                conn.execute(text("""
                    INSERT INTO "CompanyScrapingdog" (
                        "id", "companyId", "companyName", "universalNameId", "profilePhoto", "backgroundCoverImage", "industry",
                        "industries", "type", "tagline", "location", "companySize", "companySizeLinkedIn", "followerCount",
                        "website", "founded", "headquarters", "about", "specialties", "linkedinInternalId",
                        "locations", "employees", "updates", "similarCompanies", "affiliatedCompanies", "products", "createdAt", "updatedAt"
                    ) VALUES (
                        :id, :companyId, :companyName, :universalNameId, :profilePhoto, :backgroundCoverImage, :industry,
                        :industries, :type, :tagline, :location, :companySize, :companySizeLinkedIn, :followerCount,
                        :website, :founded, :headquarters, :about, :specialties, :linkedinInternalId,
                        :locations, :employees, :updates, :similarCompanies, :affiliatedCompanies, :products, now(), now()
                    )
                """), {
                    "id": cuid(),
                    "companyId": company_id,
                    "companyName": extract_field(data, "company_name", name),
                    "universalNameId": extract_field(data, "universal_name_id"),
                    "profilePhoto": extract_field(data, "profile_photo"),
                    "backgroundCoverImage": extract_field(data, "background_cover_image_url"),
                    "industry": extract_field(data, "industry"),
                    "industries": extract_field(data, "industries"),
                    "type": extract_field(data, "type"),
                    "tagline": extract_field(data, "tagline"),
                    "location": extract_field(data, "location"),
                    "companySize": extract_field(data, "company_size"),
                    "companySizeLinkedIn": extract_field(data, "company_size_on_linkedin"),
                    "followerCount": extract_field(data, "follower_count"),
                    "website": extract_field(data, "website"),
                    "founded": extract_field(data, "founded"),
                    "headquarters": extract_field(data, "headquarters"),
                    "about": extract_field(data, "about"),
                    "specialties": extract_field(data, "specialties"),
                    "linkedinInternalId": extract_field(data, "linkedin_internal_id"),
                    "locations": json.dumps(extract_field(data, "locations", [])),
                    "employees": json.dumps(extract_field(data, "employees", [])),
                    "updates": json.dumps(extract_field(data, "updates", [])),
                    "similarCompanies": json.dumps(extract_field(data, "similar_companies", [])),
                    "affiliatedCompanies": json.dumps(extract_field(data, "affiliated_companies", [])),
                    "products": json.dumps(extract_field(data, "product", []))
                })
                conn.commit()
                pbar.set_description(f"✅ {name} inserted")
            except Exception as e:
                conn.rollback()
                logger.error(f"❌ Failed to insert {name}: {str(e)}")
                continue

            time.sleep(1)

if __name__ == "__main__":
    import_scrapingdog_data()
