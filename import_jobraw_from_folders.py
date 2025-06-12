import os
import pandas as pd
from sqlalchemy import create_engine, text
from cuid import cuid
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

# --- CONFIG ---
DATABASE_URL = os.getenv("DATABASE_URL")
DATA_DIR = "data/output/20250528_043403"

# --- Connect DB ---
engine = create_engine(DATABASE_URL)

# --- Helper ---
def get_safe(val):
    if pd.isna(val):
        return None
    return str(val).strip()

# --- Main Importer ---
def import_jobraw_from_folders():
    with engine.begin() as conn:
        company_folders = [f for f in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, f))]
        
        pbar = tqdm(company_folders, desc="Processing companies", unit="company")
        for company_folder in pbar:
            folder_path = os.path.join(DATA_DIR, company_folder)
            csv_path = os.path.join(folder_path, "linkedin_jobs.csv")
            if not os.path.exists(csv_path):
                print(f"⚠️ Skipping {company_folder} (no linkedin_jobs.csv)")
                continue

            df = pd.read_csv(csv_path)
            total_jobs = len(df)

            # Find matching companyId from DB
            result = conn.execute(
                text("""SELECT id FROM "Company" WHERE "organizationName" ILIKE :name"""),
                {"name": company_folder}
            ).fetchone()

            company_id = result[0] if result else None

            for job_idx, (_, row) in enumerate(df.iterrows(), 1):
                pbar.set_description(f"Processing {company_folder} jobs ({job_idx}/{total_jobs})")
                job_id = cuid()
                conn.execute(
                    text("""
                        INSERT INTO "JobRaw" (
                            "id", "linkedinJobUrl", "jobId", "jobTitle", "companyName",
                            "location", "workArrangement", "contractType", "seniorityLevel",
                            "companyApplyUrl", "description", "companyId", "createdAt"
                        ) VALUES (
                            :id, :linkedinJobUrl, :jobId, :jobTitle, :companyName,
                            :location, :workArrangement, :contractType, :seniorityLevel,
                            :companyApplyUrl, :description, :companyId, now()
                        )
                    """),
                    {
                        "id": job_id,
                        "linkedinJobUrl": get_safe(row["linkedin_job_url"]),
                        "jobId": get_safe(row["job_id"]),
                        "jobTitle": get_safe(row["job_title"]),
                        "companyName": get_safe(row["company_name"]),
                        "location": get_safe(row.get("location")),
                        "workArrangement": get_safe(row.get("work_arrangement")),
                        "contractType": get_safe(row.get("contract_type")),
                        "seniorityLevel": get_safe(row.get("seniority_level")),
                        "companyApplyUrl": get_safe(row.get("company_apply_url")),
                        "description": get_safe(row.get("description")),
                        "companyId": company_id,
                    }
                )

if __name__ == "__main__":
    import_jobraw_from_folders()
