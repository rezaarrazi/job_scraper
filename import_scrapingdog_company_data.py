import requests
import json
import time
import os
import pandas as pd
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

# Mapping from CSV columns to JSON keys used by the ScrapingDog API
CSV_TO_JSON_MAPPING = {
    'id': 'id',  # This should typically be generated with cuid()
    'companyName': 'company_name',
    'universalNameId': 'universal_name_id',
    'profilePhoto': 'profile_photo',
    'backgroundCoverImage': 'background_cover_image_url',
    'industry': 'industry',
    'industries': 'industries',
    'type': 'type',
    'tagline': 'tagline',
    'location': 'location',
    'companySize': 'company_size',
    'companySizeLinkedIn': 'company_size_on_linkedin',
    'followerCount': 'follower_count',
    'website': 'website',
    'founded': 'founded',
    'headquarters': 'headquarters',
    'about': 'about',
    'specialties': 'specialties',
    'linkedinInternalId': 'linkedin_internal_id',
    'locations': 'locations',
    'employees': 'employees',
    'updates': 'updates',
    'similarCompanies': 'similar_companies',
    'affiliatedCompanies': 'affiliated_companies',
    'products': 'product',
    'companyId': 'companyId',
    'description': 'description',
}

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

def import_scrapingdog_data(data_source="api", csv_path=None):
    with engine.connect() as conn:
        if data_source == "api":
            companies = conn.execute(text("""
                SELECT c.id, c."linkedin", c."organizationName"
                FROM "Company" c
                LEFT JOIN "CompanyScrapingdog" s ON s."companyId" = c.id
                WHERE c."linkedin" IS NOT NULL AND s."id" IS NULL
                LIMIT 1
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
                data['linkedinUrl'] = linkedin
                insert_company_data(conn, company_id, data, name)
                time.sleep(1)
                
        elif data_source == "file":
            if not csv_path:
                logger.error("CSV path must be provided when using 'file' data source")
                return
                
            try:
                # Load CSV data into dataframe
                df = pd.read_csv(csv_path)
                logger.info(f"Loaded {len(df)} records from CSV file")
                
                # First, get all companies that don't have ScrapingDog data
                companies = conn.execute(text("""
                    SELECT c.id, c."linkedin", c."organizationName"
                    FROM "Company" c
                    LEFT JOIN "CompanyScrapingdog" s ON s."companyId" = c.id
                    WHERE c."linkedin" IS NOT NULL AND s."id" IS NULL
                """)).fetchall()
                
                logger.info(f"Found {len(companies)} companies needing ScrapingDog data")
                pbar = tqdm(companies, desc="📄 Processing companies from CSV")
                
                for company_id, linkedin, name in pbar:
                    pbar.set_description(f"📄 {name}")
                    
                    # Extract universalNameId from linkedin URL
                    universal_name_id = linkedin.strip("/").split("/")[-1]
                    
                    # Find matching record in CSV by universalNameId
                    matching_rows = df[df['universalNameId'] == universal_name_id]
                    
                    if matching_rows.empty:
                        logger.warning(f"⚠️ Skipping {name}, no matching record found in CSV. {universal_name_id}, {linkedin}")
                        continue
                    
                    # Use the first matching row
                    row = matching_rows.iloc[0]
                    
                    # Convert row to dict and map CSV columns to expected JSON structure
                    row_dict = row.to_dict()
                    data = {}
                    
                    # Map CSV columns to the expected JSON keys
                    for csv_col, json_key in CSV_TO_JSON_MAPPING.items():
                        if csv_col in row_dict:
                            data[json_key] = row_dict[csv_col]
                    
                    data['linkedinUrl'] = linkedin
                    
                    insert_company_data(conn, company_id, data, name)
                    
            except Exception as e:
                logger.error(f"❌ Failed to process CSV file: {str(e)}")
                return
        else:
            logger.error(f"Invalid data source: {data_source}. Must be 'api' or 'file'")

def insert_company_data(conn, company_id, data, name):
    try:
        conn.execute(text("""
            INSERT INTO "CompanyScrapingdog" (
                "id", "companyId", "linkedinUrl", "companyName", "universalNameId", "profilePhoto", "backgroundCoverImage", "industry",
                "industries", "type", "tagline", "location", "companySize", "companySizeLinkedIn", "followerCount",
                "website", "founded", "headquarters", "about", "specialties", "linkedinInternalId",
                "locations", "employees", "updates", "similarCompanies", "affiliatedCompanies", "products", "createdAt", "updatedAt"
            ) VALUES (
                :id, :companyId, :linkedinUrl, :companyName, :universalNameId, :profilePhoto, :backgroundCoverImage, :industry,
                :industries, :type, :tagline, :location, :companySize, :companySizeLinkedIn, :followerCount,
                :website, :founded, :headquarters, :about, :specialties, :linkedinInternalId,
                :locations, :employees, :updates, :similarCompanies, :affiliatedCompanies, :products, now(), now()
            )
        """), {
            "id": cuid(),
            "companyId": company_id,
            "linkedinUrl": extract_field(data, "linkedinUrl"),
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
        logger.info(f"✅ {name} inserted")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Failed to insert {name}: {str(e)}")
        return False

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Import ScrapingDog company data')
    parser.add_argument('--source', choices=['api', 'file'], default='api', help='Data source (api or file)')
    parser.add_argument('--csv', help='Path to CSV file when using file source')
    
    args = parser.parse_args()
    
    import_scrapingdog_data(data_source=args.source, csv_path=args.csv)
