"""Database Seeder for Callora Voice Agent Knowledge Base.

Seeds verified business knowledge items into the database so the AI voice
agent can answer customer questions on live phone calls.
"""

import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.models  # Register core models
import app.modules.voice.models  # Register voice models
from app.modules.voice.constants import KnowledgeCategory
from app.modules.voice.models import Business, BusinessKnowledgeItem, prefixed_id

SEED_BUSINESS_ID = "biz_001"

SAMPLE_KNOWLEDGE_ITEMS = [
    # --- PRICING ---
    {
        "category": KnowledgeCategory.PRICING,
        "title": "Starter Plan Pricing",
        "content": "Our Starter plan costs ₹5,000 per month for up to 5 team members, including automated call campaigns and basic CRM integration.",
        "tags": ["pricing", "starter", "cost", "plan", "price", "5000", "cheap"],
        "source_reference": "Callora Pricing Guide 2026",
        "priority": 10,
    },
    {
        "category": KnowledgeCategory.PRICING,
        "title": "Professional Plan Pricing",
        "content": "The Professional plan is ₹12,000 per month for up to 20 users with automated CRM sync, multilingual voice calling, and live agent handoffs.",
        "tags": ["pricing", "professional", "cost", "plan", "price", "12000", "crm"],
        "source_reference": "Callora Pricing Guide 2026",
        "priority": 9,
    },
    {
        "category": KnowledgeCategory.PRICING,
        "title": "Enterprise Plan Pricing",
        "content": "Our Enterprise plan costs ₹30,000 per month for unlimited users, custom AI voice tuning, dedicated account management, and SLA guarantees.",
        "tags": ["pricing", "enterprise", "cost", "plan", "price", "30000", "custom"],
        "source_reference": "Callora Pricing Guide 2026",
        "priority": 8,
    },
    {
        "category": KnowledgeCategory.PRICING,
        "title": "Payment Options and Annual Discount",
        "content": "We accept UPI, NEFT, Net Banking, and Credit Cards. Annual upfront subscriptions receive a 15% discount with official GST invoices provided.",
        "tags": ["payment", "discount", "annual", "gst", "upi", "card", "billing"],
        "source_reference": "Callora Billing Policy",
        "priority": 7,
    },
    {
        "category": KnowledgeCategory.PRICING,
        "title": "Pan-India Bank Home Loan Tie-Ups & Pre-Approvals",
        "content": "UrbanNest Realty projects have official pre-approvals with India's top nationalized and private banks including SBI, HDFC Bank, ICICI Bank, Axis Bank, and Bank of Baroda. Buyers enjoy competitive home loan interest rates starting from 8.35% per annum, funding up to 90% of property cost, and zero processing fee festive offers.",
        "tags": ["home loan", "loan", "bank", "sbi", "hdfc", "icici", "axis", "interest rate", "financing", "emi", "pre-approved", "8.35%", "funding", "mortgage"],
        "source_reference": "UrbanNest Banking & Finance Directory 2026",
        "priority": 10,
    },

    # --- PRODUCT FEATURES ---
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Multilingual Voice Support",
        "content": "Callora natively supports English, Hindi, and Kannada with natural accent adaptation and automatic code-switching during live phone calls.",
        "tags": ["multilingual", "languages", "hindi", "kannada", "english", "voice", "accent"],
        "source_reference": "Callora Technical Specifications",
        "priority": 10,
    },
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "CRM and Lead Integration",
        "content": "Callora integrates seamlessly with HubSpot, Salesforce, Zoho CRM, Google Sheets, and custom webhook APIs for real-time lead synchronization.",
        "tags": ["crm", "integration", "hubspot", "salesforce", "zoho", "sync", "api", "webhook"],
        "source_reference": "Callora Integrations Directory",
        "priority": 9,
    },
    # --- PAN-INDIA PROPERTY INVENTORIES ---
    # South India
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Bengaluru Property Inventory",
        "content": "UrbanNest Realty offers 2BHK, 3BHK, and 4BHK luxury apartments and penthouses in Bengaluru starting from ₹85 Lakhs up to ₹2.8 Crores across prime tech hubs including Whitefield, Sarjapur Road, Electronic City, Bellandur, and North Bengaluru near Manyata Tech Park and Devanahalli Airport corridor.",
        "tags": ["property", "properties", "flat", "apartment", "2bhk", "3bhk", "4bhk", "penthouse", "bengaluru", "bangalore", "whitefield", "sarjapur", "electronic city", "manyata", "devanahalli", "bellandur", "karnataka", "lakh", "crore"],
        "source_reference": "UrbanNest Bengaluru Catalog 2026",
        "priority": 10,
    },
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Hyderabad Property Inventory",
        "content": "UrbanNest Realty features premium 2BHK, 3BHK, and 4BHK high-rise gated communities and luxury duplex villas in Hyderabad starting from ₹78 Lakhs up to ₹3.2 Crores in HITEC City, Gachibowli, Financial District, Tellapur, Kondapur, and Jubilee Hills with 100% Vaastu compliance.",
        "tags": ["property", "properties", "flat", "apartment", "villa", "2bhk", "3bhk", "4bhk", "hyderabad", "hitec city", "gachibowli", "financial district", "tellapur", "kondapur", "jubilee hills", "cyberabad", "telangana", "vaastu"],
        "source_reference": "UrbanNest Hyderabad Catalog 2026",
        "priority": 10,
    },
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Chennai Property Inventory",
        "content": "UrbanNest Realty offers contemporary 2BHK and 3BHK apartments and beachfront luxury villas in Chennai starting from ₹65 Lakhs up to ₹2.1 Crores along the OMR IT Expressway, Anna Nagar, Porur, Guindy, and East Coast Road (ECR).",
        "tags": ["property", "properties", "flat", "apartment", "villa", "2bhk", "3bhk", "chennai", "omr", "anna nagar", "porur", "guindy", "ecr", "tamil nadu", "madras", "beachfront"],
        "source_reference": "UrbanNest Chennai Catalog 2026",
        "priority": 9,
    },
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Kochi & Kerala Property Inventory",
        "content": "UrbanNest Realty provides waterfront 2BHK and 3BHK luxury apartments and penthouses in Kochi starting from ₹58 Lakhs up to ₹1.75 Crores located near Kakkanad InfoPark, Marine Drive, and Panampilly Nagar with panoramic backwater views and private boat jetty access.",
        "tags": ["property", "properties", "flat", "apartment", "2bhk", "3bhk", "kochi", "cochin", "kerala", "kakkanad", "infopark", "marine drive", "panampilly", "waterfront", "backwaters"],
        "source_reference": "UrbanNest Kerala Catalog 2026",
        "priority": 9,
    },

    # West India
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Mumbai MMR Property Inventory",
        "content": "UrbanNest Realty presents sea-view luxury 2BHK and 3BHK residences in Mumbai starting from ₹1.4 Crores in Andheri and Powai, ultra-luxury high-rises in Worli and Bandra West from ₹3.8 Crores, and integrated smart townships in Thane (Ghodbunder Road) and Navi Mumbai (Kharghar) starting from ₹68 Lakhs.",
        "tags": ["property", "properties", "flat", "apartment", "mumbai", "bandra", "worli", "lower parel", "andheri", "powai", "thane", "navi mumbai", "kharghar", "ghodbunder", "maharashtra", "bombay", "sea view", "luxury", "mmr"],
        "source_reference": "UrbanNest Mumbai MMR Catalog 2026",
        "priority": 10,
    },
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Pune Property Inventory",
        "content": "UrbanNest Realty offers 2BHK and 3BHK premium residences and garden apartments in Pune starting from ₹60 Lakhs up to ₹1.45 Crores in major IT corridors including Hinjawadi IT Park, Wakad, Baner, Kharadi, and Koregaon Park with EV charging stations and extensive clubhouses.",
        "tags": ["property", "properties", "flat", "apartment", "2bhk", "3bhk", "pune", "hinjawadi", "wakad", "baner", "kharadi", "koregaon park", "maharashtra", "it park", "clubhouse"],
        "source_reference": "UrbanNest Pune Catalog 2026",
        "priority": 9,
    },
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Ahmedabad & GIFT City Property Inventory",
        "content": "UrbanNest Realty features 2BHK and 3BHK smart residences and commercial suites starting from ₹52 Lakhs along SG Highway, Prahlad Nagar, and within India's premier international financial tech hub GIFT City Gandhinagar offering competitive rental yields and tax benefits.",
        "tags": ["property", "properties", "flat", "apartment", "commercial", "2bhk", "3bhk", "ahmedabad", "gift city", "gandhinagar", "sg highway", "prahlad nagar", "gujarat", "investment"],
        "source_reference": "UrbanNest Gujarat Catalog 2026",
        "priority": 9,
    },

    # North India
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Delhi NCR Property Inventory",
        "content": "UrbanNest Realty offers luxury 3BHK and 4BHK residences along Gurugram Golf Course Extension Road and Dwarka Expressway starting from ₹1.85 Crores up to ₹4.5 Crores, and high-rise gated communities in Noida Sector 62 and Sector 150 Expressway starting from ₹75 Lakhs.",
        "tags": ["property", "properties", "flat", "apartment", "delhi", "delhi ncr", "gurugram", "gurgaon", "noida", "greater noida", "golf course road", "dwarka expressway", "sector 150", "3bhk", "4bhk", "ncr", "luxury"],
        "source_reference": "UrbanNest Delhi NCR Catalog 2026",
        "priority": 10,
    },
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Chandigarh Tri-City Property Inventory",
        "content": "UrbanNest Realty presents independent luxury floors and 3BHK apartments in Chandigarh Tri-City starting from ₹65 Lakhs located in Mohali IT City, Panchkula Sector 20, and New Chandigarh with wide tree-lined avenues and park-facing plots.",
        "tags": ["property", "properties", "flat", "apartment", "floors", "3bhk", "chandigarh", "mohali", "panchkula", "punjab", "haryana", "tri-city", "it city"],
        "source_reference": "UrbanNest Chandigarh Catalog 2026",
        "priority": 8,
    },
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Jaipur Property Inventory",
        "content": "UrbanNest Realty provides 2BHK and 3BHK heritage-inspired modern villas and gated apartments in Jaipur starting from ₹45 Lakhs situated in Jagatpura, Vaishali Nagar, and Mansarovar close to Jaipur International Airport.",
        "tags": ["property", "properties", "flat", "apartment", "villa", "2bhk", "3bhk", "jaipur", "rajasthan", "jagatpura", "vaishali nagar", "mansarovar", "pink city"],
        "source_reference": "UrbanNest Rajasthan Catalog 2026",
        "priority": 8,
    },
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Lucknow Property Inventory",
        "content": "UrbanNest Realty offers 2BHK and 3BHK modern residences and duplex villas in Lucknow starting from ₹55 Lakhs in Gomti Nagar Extension, Shaheed Path, and Sushant Golf City equipped with world-class sports facilities and 24/7 security.",
        "tags": ["property", "properties", "flat", "apartment", "villa", "duplex", "2bhk", "3bhk", "lucknow", "uttar pradesh", "gomti nagar", "shaheed path", "sushant golf city", "up"],
        "source_reference": "UrbanNest Uttar Pradesh Catalog 2026",
        "priority": 8,
    },

    # East & Central India
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Kolkata Property Inventory",
        "content": "UrbanNest Realty provides 2BHK and 3BHK eco-friendly apartments in Kolkata starting from ₹50 Lakhs up to ₹1.8 Crores located in New Town Rajarhat, Salt Lake Sector V, EM Bypass, and luxury boutique developments in South Kolkata Alipore.",
        "tags": ["property", "properties", "flat", "apartment", "2bhk", "3bhk", "kolkata", "calcutta", "west bengal", "new town", "rajarhat", "salt lake", "sector v", "em bypass", "alipore"],
        "source_reference": "UrbanNest Kolkata Catalog 2026",
        "priority": 9,
    },
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Bhubaneswar Property Inventory",
        "content": "UrbanNest Realty features 2BHK and 3BHK smart-living apartments in Bhubaneswar starting from ₹48 Lakhs near Infocity, Patia, and Chandrasekharpur with dedicated co-working spaces and landscaped gardens.",
        "tags": ["property", "properties", "flat", "apartment", "2bhk", "3bhk", "bhubaneswar", "odisha", "patia", "infocity", "chandrasekharpur", "smart city"],
        "source_reference": "UrbanNest Odisha Catalog 2026",
        "priority": 8,
    },
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Indore Property Inventory",
        "content": "UrbanNest Realty offers 2BHK and 3BHK premium apartments and residential plots in Indore starting from ₹42 Lakhs along the Super Corridor, Vijay Nagar, and AB Bypass Road near top educational institutes and IT SEZs.",
        "tags": ["property", "properties", "flat", "apartment", "plot", "2bhk", "3bhk", "indore", "madhya pradesh", "super corridor", "vijay nagar", "ab road", "mp"],
        "source_reference": "UrbanNest Central India Catalog 2026",
        "priority": 8,
    },

    # Pan-India Commercial & Co-working
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Pan-India Commercial & Tech Park Office Inventory",
        "content": "UrbanNest Realty offers Grade-A commercial office spaces, co-working suites, and retail storefronts across India's top tech corridors including Bengaluru Outer Ring Road, Cyber City Gurugram, HITEC City Hyderabad, Bandra Kurla Complex (BKC) Mumbai, and Hinjawadi Pune with high rental yields.",
        "tags": ["commercial", "office", "retail", "co-working", "tech park", "bkc", "cyber city", "outer ring road", "hitec city", "hinjawadi", "shop", "rental yield", "investment"],
        "source_reference": "UrbanNest Commercial Properties Directory 2026",
        "priority": 9,
    },

    # Pan-India NRI Desk
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Pan-India NRI Property Buying Desk",
        "content": "UrbanNest Realty operates a dedicated NRI investment desk fully compliant with RBI and FEMA regulations. We assist Non-Resident Indians across the UAE, USA, UK, Singapore, and Europe with NRE/NRO rupee accounts, Power of Attorney (POA) legal execution, 360-degree high-definition virtual tours, and hassle-free repatriation of rental yields and capital gains.",
        "tags": ["nri", "non-resident", "fema", "rbi", "nre", "nro", "poa", "power of attorney", "virtual tour", "dubai", "usa", "uk", "overseas", "investment", "repatriation"],
        "source_reference": "UrbanNest NRI Global Advisory Guide 2026",
        "priority": 9,
    },

    # Pan-India Customer Amenities & Site Visits
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Complimentary Metro Site Visits and Cab Pickup",
        "content": "UrbanNest Realty offers complimentary chauffeur-driven door-to-door cab pickup and drop-off services for site visits across major Indian metropolitan areas including Bengaluru, Mumbai, Delhi NCR, Hyderabad, Chennai, Pune, and Kolkata, accompanied by a senior property advisor.",
        "tags": ["site visit", "cab", "pickup", "drop", "free visit", "transport", "metro", "schedule visit", "tour", "inspection", "car", "chauffeur"],
        "source_reference": "UrbanNest Site Visit Customer Policy",
        "priority": 9,
    },

    # Pan-India Construction & Sustainability
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Pan-India Green Building & Earthquake Resistant Construction",
        "content": "All UrbanNest developments across India are constructed using advanced Mivan aluminum shuttering or earthquake-resistant RCC frame structures complying with Bureau of Indian Standards (BIS) seismic zone codes, and are IGBC or GRIHA certified for solar energy, rainwater harvesting, and energy efficiency.",
        "tags": ["construction", "quality", "mivan", "earthquake", "bis", "igbc", "griha", "green building", "rainwater harvesting", "solar", "safety", "structural"],
        "source_reference": "UrbanNest Engineering & Sustainability Standards 2026",
        "priority": 8,
    },

    # Pan-India Multilingual AI Voice
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Pan-India Regional Languages and Voice Support",
        "content": "Callora Voice agent speaks and understands major Indian languages including English, Hindi, Kannada, Tamil, Telugu, Marathi, and Bengali with native regional accents, colloquial phrasing, and seamless real-time code-switching during live sales phone calls.",
        "tags": ["languages", "regional", "hindi", "kannada", "tamil", "telugu", "marathi", "bengali", "english", "multilingual", "code-switching", "accent", "india", "dialects"],
        "source_reference": "Callora Multilingual Voice Engine 2026",
        "priority": 10,
    },

    # --- POLICIES ---
    {
        "category": KnowledgeCategory.POLICY,
        "title": "Pan-India RERA Clearances and Approvals",
        "content": "Every UrbanNest Realty project across India is 100% compliant and registered with respective State RERA authorities: MahaRERA (Maharashtra), K-RERA (Karnataka), UP-RERA (Uttar Pradesh), HRERA (Haryana), TS-RERA (Telangana), TNRERA (Tamil Nadu), and WBRERA (West Bengal), guaranteeing transparent possession timelines, bank escrow protection, and verified title deeds.",
        "tags": ["rera", "maharera", "k-rera", "up-rera", "hrera", "ts-rera", "tnrera", "wbrera", "compliance", "legal", "approval", "possession", "escrow", "title", "safety", "law"],
        "source_reference": "UrbanNest Pan-India Legal & Compliance Manual 2026",
        "priority": 10,
    },
    {
        "category": KnowledgeCategory.POLICY,
        "title": "Stamp Duty and Property Registration Across States",
        "content": "Stamp duty and registration fees vary by state across India: Maharashtra charges 5% to 6% with 1% concessions for women buyers, Karnataka charges 5%, Delhi NCR and Uttar Pradesh range from 5% to 7%, Telangana is 6%, and Tamil Nadu is 7%. UrbanNest provides complete legal documentation and on-ground registration support.",
        "tags": ["stamp duty", "registration", "tax", "charges", "fees", "legal", "documentation", "maharashtra", "karnataka", "delhi", "telangana", "tamil nadu", "women concession"],
        "source_reference": "UrbanNest Taxation & Registration Handbook 2026",
        "priority": 9,
    },
    {
        "category": KnowledgeCategory.POLICY,
        "title": "Pan-India TRAI Telephony & National DND Compliance",
        "content": "Callora strictly complies with Telecom Regulatory Authority of India (TRAI) directives and Telecom Commercial Communications Customer Preference Regulations (TCCCPR). Automated sales calls are strictly placed between 9:00 AM and 8:00 PM IST on authorized business days, honoring national DND registries and immediate verbal opt-outs.",
        "tags": ["trai", "tcccpr", "dnd", "calling hours", "compliance", "telephony", "9 am to 8 pm", "regulations", "opt out", "telecom", "calling window", "dpdp"],
        "source_reference": "Callora Telecom Regulatory Manual 2026",
        "priority": 10,
    },
    {
        "category": KnowledgeCategory.POLICY,
        "title": "Refund Policy and Money-Back Guarantee",
        "content": "We offer a 14-day full money-back guarantee with zero cancellation fees if you are not completely satisfied with our platform.",
        "tags": ["refund", "guarantee", "cancellation", "policy", "money-back", "14-day"],
        "source_reference": "Callora Terms of Service",
        "priority": 10,
    },
    {
        "category": KnowledgeCategory.POLICY,
        "title": "Data Privacy and TRAI Compliance",
        "content": "Callora is fully compliant with Indian DPDP regulations and strictly adheres to TRAI calling window guidelines between 9 AM and 8 PM.",
        "tags": ["privacy", "security", "trai", "compliance", "calling hours", "dnd", "dpdp"],
        "source_reference": "Callora Compliance & Privacy Manual",
        "priority": 9,
    },
    {
        "category": KnowledgeCategory.POLICY,
        "title": "Subscription Cancellation Policy",
        "content": "You can cancel your subscription at any time directly from the dashboard with no lock-in contract or termination penalties.",
        "tags": ["cancel", "cancellation", "contract", "lock-in", "policy"],
        "source_reference": "Callora Terms of Service",
        "priority": 8,
    },

    # --- ELIGIBILITY ---
    {
        "category": KnowledgeCategory.ELIGIBILITY,
        "title": "Pradhan Mantri Awas Yojana (PMAY) Subsidies",
        "content": "Eligible first-time homebuyers across all Indian states can avail interest subsidies of up to ₹2.67 Lakhs under the Central Government Pradhan Mantri Awas Yojana (PMAY) Credit Linked Subsidy Scheme across qualifying EWS, LIG, and MIG income categories.",
        "tags": ["pmay", "subsidy", "government subsidy", "first-time buyer", "pradhan mantri awas yojana", "credit linked", "eligibility", "2.67 lakh", "ews", "lig", "mig"],
        "source_reference": "Ministry of Housing and Urban Affairs PMAY Guidelines",
        "priority": 9,
    },
    {
        "category": KnowledgeCategory.ELIGIBILITY,
        "title": "System and Hardware Requirements",
        "content": "Callora is a 100% cloud-hosted SaaS platform. There is no on-premise hardware, PBX, or server requirement; all you need is a modern web browser.",
        "tags": ["requirements", "hardware", "server", "prerequisites", "cloud", "browser"],
        "source_reference": "Callora Architecture Guide",
        "priority": 9,
    },

    # --- OBJECTION HANDLING ---
    {
        "category": KnowledgeCategory.OBJECTION_HANDLING,
        "title": "Why Choose Callora Over Traditional Robocallers",
        "content": "Unlike rigid IVR robocallers, Callora uses ultra-low-latency conversational AI that understands interruptions, handles objections naturally, and speaks Indian regional languages.",
        "tags": ["competitor", "alternative", "robocaller", "difference", "better", "why choose"],
        "source_reference": "Callora Product Comparison",
        "priority": 9,
    },
    {
        "category": KnowledgeCategory.OBJECTION_HANDLING,
        "title": "Return on Investment (ROI)",
        "content": "Callora reduces sales calling labor costs by over 70% while improving qualified lead conversion rates by 3x through instant automated outbound engagement.",
        "tags": ["roi", "cost", "expensive", "worth it", "value", "labor", "conversion"],
        "source_reference": "Callora Customer Case Studies",
        "priority": 8,
    },

    # --- ESCALATION RULE ---
    {
        "category": KnowledgeCategory.ESCALATION_RULE,
        "title": "Human Representative Escalation",
        "content": "If you wish to speak with a human agent, manager, or property specialist, we can arrange an immediate warm transfer or schedule a callback at your convenience.",
        "tags": ["human", "manager", "agent", "supervisor", "representative", "transfer", "callback", "speak"],
        "source_reference": "Callora Escalation SOP",
        "priority": 10,
    },
]


def seed_database(db_url: str):
    print(f"\n--- Seeding database at: {db_url} ---")
    kwargs = {}
    if db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    
    engine = create_engine(db_url, **kwargs)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        # 1. Ensure business exists
        business = session.query(Business).filter_by(id=SEED_BUSINESS_ID).first()
        if not business:
            business = Business(
                id=SEED_BUSINESS_ID,
                name="UrbanNest Realty / Callora Voice",
                bolna_agent_id="4f11f427-469e-471d-9548-12610d645d65",
                timezone="Asia/Kolkata",
                default_language="en",
                supported_languages=["en", "hi", "kn"],
                calling_window_start=9,
                calling_window_end=20,
                max_calls_per_lead_per_day=3,
                max_calls_per_business_per_day=500,
                max_concurrent_calls=5,
                warm_transfer_enabled=True,
                handoff_target_number="+918071580188",
            )
            session.add(business)
            session.flush()
            print(f"Created business: {business.id} ({business.name})")
        else:
            business.bolna_agent_id = "4f11f427-469e-471d-9548-12610d645d65"
            print(f"Found existing business: {business.id} ({business.name}), updated bolna_agent_id")

        # 2. Insert knowledge items
        inserted_count = 0
        updated_count = 0

        for item_data in SAMPLE_KNOWLEDGE_ITEMS:
            existing = session.query(BusinessKnowledgeItem).filter_by(
                business_id=SEED_BUSINESS_ID,
                title=item_data["title"],
            ).first()

            if existing:
                existing.category = item_data["category"]
                existing.content = item_data["content"]
                existing.tags = item_data["tags"]
                existing.source_reference = item_data["source_reference"]
                existing.priority = item_data["priority"]
                existing.is_active = True
                updated_count += 1
            else:
                new_item = BusinessKnowledgeItem(
                    id=prefixed_id("kb"),
                    business_id=SEED_BUSINESS_ID,
                    category=item_data["category"],
                    title=item_data["title"],
                    content=item_data["content"],
                    tags=item_data["tags"],
                    source_reference=item_data["source_reference"],
                    priority=item_data["priority"],
                    is_active=True,
                )
                session.add(new_item)
                inserted_count += 1

        session.commit()
        total_items = session.query(BusinessKnowledgeItem).filter_by(business_id=SEED_BUSINESS_ID).count()
        print(f"Seeding completed successfully!")
        print(f"Inserted: {inserted_count}, Updated: {updated_count}, Total Knowledge Items in DB: {total_items}")
        return total_items


if __name__ == "__main__":
    db_target = sys.argv[1] if len(sys.argv) > 1 else None
    if not db_target:
        # Default to local sqlite file
        db_path = backend_dir.parent / "callora.db"
        db_target = f"sqlite:///{db_path}"

    seed_database(db_target)
