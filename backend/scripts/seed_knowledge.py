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
    {
        "category": KnowledgeCategory.PRODUCT_FEATURE,
        "title": "Bengaluru Property Inventory",
        "content": "UrbanNest Realty offers 2BHK and 3BHK luxury apartments in Bengaluru starting from ₹85 Lakhs, located in prime tech hubs like Whitefield and Sarjapur.",
        "tags": ["property", "properties", "flat", "apartment", "2bhk", "3bhk", "bengaluru", "bangalore", "whitefield", "sarjapur", "lakh"],
        "source_reference": "UrbanNest Property Catalog",
        "priority": 9,
    },

    # --- POLICIES ---
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
