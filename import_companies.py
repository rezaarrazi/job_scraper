import os
import pandas as pd
from sqlalchemy import create_engine, text
from cuid import cuid
import argparse
from dotenv import load_dotenv

load_dotenv()

# Parse command line arguments
parser = argparse.ArgumentParser(description='Import companies from CSV to database')
parser.add_argument('file_path', help='Path to the CSV file containing company data')
args = parser.parse_args()

# Load CSV
df = pd.read_csv(args.file_path)
print(df.head())

# Setup DB connection - Updated for Supabase
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

def safe_date(val):
    try:
        return pd.to_datetime(val).to_pydatetime() if pd.notna(val) else None
    except:
        return None

def safe_int(val):
    try:
        return int(val) if pd.notna(val) else None
    except:
        return None

def safe_str(val):
    return str(val).strip() if pd.notna(val) else None

def safe_list(val):
    return [s.strip() for s in str(val).split(',')] if pd.notna(val) else []

# Insert companies
with engine.begin() as conn:
    for idx, row in df.iterrows():
        query = text("""
    INSERT INTO "Company" (
                "id",
                "organizationName",
                "organizationNameUrl",
                "Investmentstage",
                "industries",
                "headquartersLocation",
                "description",
                "fullDescription",
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
                "lastFundingDate",
                "lastFundingAmountUSD",
                "lastFundingType",
                "lastEquityFundingAmountUSD",
                "lastEquityFundingType",
                "totalFundingAmountUSD",
                "totalEquityFundingAmount",
                "ipoStatus",
                "ipoDate",
                "founders",
                "cbRankOrganization",
                "contactEmail",
                "contactJobDepartments",
                "estimatedRevenueRange",
                "foundedDate",
                "numberOfInvestors",
                "numberOfAcquisitions",
                "phoneNumber",
                "createdAt"
            ) VALUES (
                :id,
                :organizationName,
                :organizationNameUrl,
                :Investmentstage,
                :industries,
                :headquartersLocation,
                :description,
                :fullDescription,
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
                :lastFundingDate,
                :lastFundingAmountUSD,
                :lastFundingType,
                :lastEquityFundingAmountUSD,
                :lastEquityFundingType,
                :totalFundingAmountUSD,
                :totalEquityFundingAmount,
                :ipoStatus,
                :ipoDate,
                :founders,
                :cbRankOrganization,
                :contactEmail,
                :contactJobDepartments,
                :estimatedRevenueRange,
                :foundedDate,
                :numberOfInvestors,
                :numberOfAcquisitions,
                :phoneNumber,
                now()
            )
        """)

        conn.execute(query, {
            "id": cuid(),
            "organizationName": safe_str(row['Organization Name']),
            "organizationNameUrl": safe_str(row['Organization Name URL']),
            "Investmentstage": safe_str(row['Investment Stage']),
            "industries": safe_str(row['Industries']),
            "headquartersLocation": safe_str(row['Headquarters Location']),
            "description": safe_str(row['Description']),
            "fullDescription": safe_str(row.get('Full Description')),
            "cbRank": safe_str(row['CB Rank (Company)']),
            "website": safe_str(row['Website']),
            "linkedin": safe_str(row['LinkedIn']),
            "facebook": safe_str(row['Facebook']),
            "twitter": safe_str(row['Twitter']),
            "companyType": safe_str(row['Company Type']),
            "activelyHiring": True if row['Actively Hiring'] == "Yes" else False if row['Actively Hiring'] == "No" else None,
            "industryGroups": safe_str(row['Industry Groups']),
            "numberOfEmployees": safe_str(row['Number of Employees']),
            "top5Investors": safe_str(row['Top 5 Investors']),
            "numberOfFundingRounds": safe_int(row['Number of Funding Rounds']),
            "fundingStatus": safe_str(row['Funding Status']),
            "lastFundingDate": safe_date(row.get('Last Funding Date')),
            "lastFundingAmountUSD": safe_str(row.get('Last Funding Amount (USD)')),
            "lastFundingType": safe_str(row.get('Last Funding Type')),
            "lastEquityFundingAmountUSD": safe_str(row.get('Last Equity Funding Amount (USD)')),
            "lastEquityFundingType": safe_str(row.get('Last Equity Funding Type')),
            "totalFundingAmountUSD": safe_str(row.get('Total Funding Amount (USD)')),
            "totalEquityFundingAmount": safe_str(row.get('Total Equity Funding Amount')),
            "ipoStatus": safe_str(row.get('IPO Status')),
            "ipoDate": safe_date(row.get('IPO Date')),
            "founders": safe_list(row.get('Founders')),
            "cbRankOrganization": safe_str(row.get('CB Rank (Organization)')),
            "contactEmail": safe_str(row.get('Contact Email')),
            "contactJobDepartments": safe_str(row.get('Contact Job Departments')),
            "estimatedRevenueRange": safe_str(row.get('Estimated Revenue Range')),
            "foundedDate": safe_date(row.get('Founded Date')),
            "numberOfInvestors": safe_int(row.get('Number of Investors')),
            "numberOfAcquisitions": safe_int(row.get('Number of Acquisitions')),
            "phoneNumber": safe_str(row.get('Phone Number')),
        })

    print(f"✅ Successfully inserted {len(df)} companies into the database.")
