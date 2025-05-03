import pandas as pd
from sqlalchemy import create_engine, text
from cuid import cuid

# Load CSV
df = pd.read_csv('./data/input/companies-4-5-2025.csv')
print(df.head())

# Setup DB connection
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/postgres"  # adjust based on your setup
engine = create_engine(DATABASE_URL)

# Function to safely extract optional fields
def get_safe(value):
    if pd.isna(value):
        return None
    return value

# Insert companies
with engine.begin() as conn:
    for idx, row in df.iterrows():
        query = text("""
    INSERT INTO "Company" (
                "id",
                "organizationName",
                "organizationNameUrl",
                "stage",
                "industries",
                "headquartersLocation",
                "description",
                "cbRank",
                "website",
                "linkedin",
                "facebook",
                "twitter",
                "companyType",
                "activelyHiring",
                "industryGroups",
                "numberOfEmployees",
                "top5Investors",
                "numberOfFundingRounds",
                "fundingStatus",
                "createdAt"
            ) VALUES (
                :id,
                :organizationName,
                :organizationNameUrl,
                :stage,
                :industries,
                :headquartersLocation,
                :description,
                :cbRank,
                :website,
                :linkedin,
                :facebook,
                :twitter,
                :companyType,
                :activelyHiring,
                :industryGroups,
                :numberOfEmployees,
                :top5Investors,
                :numberOfFundingRounds,
                :fundingStatus,
                now()
            )
        """)

        
        conn.execute(query, {
            "id": cuid(),
            "organizationName": get_safe(row['Organization Name']),
            "organizationNameUrl": get_safe(row['Organization Name URL']),
            "stage": get_safe(row['Stage']),
            "industries": get_safe(row['Industries']),
            "headquartersLocation": get_safe(row['Headquarters Location']),
            "description": get_safe(row['Description']),
            "cbRank": get_safe(row['CB Rank (Company)']),
            "website": get_safe(row['Website']),
            "linkedin": get_safe(row['LinkedIn']),
            "facebook": get_safe(row['Facebook']),
            "twitter": get_safe(row['Twitter']),
            "companyType": get_safe(row['Company Type']),
            "activelyHiring": True if row['Actively Hiring'] == "Yes" else False if row['Actively Hiring'] == "No" else None,
            "industryGroups": get_safe(row['Industry Groups']),
            "numberOfEmployees": get_safe(row['Number of Employees']),
            "top5Investors": get_safe(row['Top 5 Investors']),
            "numberOfFundingRounds": int(row['Number of Funding Rounds']) if not pd.isna(row['Number of Funding Rounds']) else None,
            "fundingStatus": get_safe(row['Funding Status']),
        })

    print(f"✅ Successfully inserted {len(df)} companies into the database.")
