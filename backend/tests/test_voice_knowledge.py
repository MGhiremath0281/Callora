"""Tests for the business knowledge base storage and retrieval.

Covers:
- Storing and retrieving verified business knowledge scoped by business_id
- Cross-business isolation (tenant A cannot see or modify tenant B knowledge)
- Batch import
- Search scoring, ranking, and category filtering
"""

import pytest

from app.modules.voice import repository as repo
from app.modules.voice.constants import KnowledgeCategory
from app.modules.voice.models import BusinessKnowledgeItem
from tests.conftest import BIZ_1, BIZ_2, KEY_1, KEY_2, auth_headers

KNOWLEDGE_URL = "/api/v1/voice/knowledge"


@pytest.fixture()
def seeded_knowledge(db, voice_business):
    """Seed test knowledge items for BIZ_1 and BIZ_2."""
    item1 = BusinessKnowledgeItem(
        business_id=BIZ_1,
        category=KnowledgeCategory.PRICING,
        title="Starter Plan",
        content="The Starter plan costs ₹5,000 per month for up to 5 team members.",
        tags=["pricing", "starter", "cost"],
        source_reference="Pricing Guide v1",
        is_active=True,
        priority=10,
    )
    item2 = BusinessKnowledgeItem(
        business_id=BIZ_1,
        category=KnowledgeCategory.POLICY,
        title="Refund Policy",
        content="Callora offers a 14-day money-back guarantee with zero cancellation fees.",
        tags=["refund", "cancellation", "policy"],
        source_reference="Refund Policy v2",
        is_active=True,
        priority=5,
    )
    item_biz2 = BusinessKnowledgeItem(
        business_id=BIZ_2,
        category=KnowledgeCategory.PRICING,
        title="Solar Package",
        content="Acme Solar residential system costs ₹1,80,000.",
        tags=["solar", "pricing"],
        source_reference="Acme Rate Card",
        is_active=True,
        priority=5,
    )
    db.add_all([item1, item2, item_biz2])
    db.commit()
    return {"biz1_items": [item1, item2], "biz2_item": item_biz2}


class TestKnowledgeCRUD:
    def test_create_knowledge_item(self, voice_client, voice_business):
        payload = {
            "category": KnowledgeCategory.PRICING,
            "title": "Enterprise Plan",
            "content": "Enterprise plan is ₹30,000 per month with dedicated support.",
            "tags": ["enterprise", "pricing"],
            "source_reference": "Pricing Sheet 2026",
            "is_active": True,
            "priority": 8,
        }
        res = voice_client.post(KNOWLEDGE_URL, json=payload, headers=auth_headers(BIZ_1))
        assert res.status_code == 201
        data = res.json()
        assert data["id"].startswith("kb_")
        assert data["business_id"] == BIZ_1
        assert data["title"] == "Enterprise Plan"
        assert data["priority"] == 8

    def test_list_knowledge_scoped_to_business(self, voice_client, seeded_knowledge):
        # BIZ_1 list
        res1 = voice_client.get(KNOWLEDGE_URL, headers=auth_headers(BIZ_1))
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["total"] == 2
        assert all(it["business_id"] == BIZ_1 for it in data1["items"])

        # BIZ_2 list
        res2 = voice_client.get(KNOWLEDGE_URL, headers=auth_headers(BIZ_2))
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["total"] == 1
        assert data2["items"][0]["business_id"] == BIZ_2

    def test_cross_business_isolation_prevented(self, voice_client, seeded_knowledge):
        biz2_item_id = seeded_knowledge["biz2_item"].id

        # BIZ_1 attempting to read BIZ_2 knowledge item is rejected with 404
        res = voice_client.get(f"{KNOWLEDGE_URL}/{biz2_item_id}", headers=auth_headers(BIZ_1))
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "knowledge_item_not_found"

        # BIZ_1 attempting to update BIZ_2 knowledge item is rejected
        res_update = voice_client.put(
            f"{KNOWLEDGE_URL}/{biz2_item_id}",
            json={"title": "Hacked Title"},
            headers=auth_headers(BIZ_1),
        )
        assert res_update.status_code == 404

        # BIZ_1 attempting to delete BIZ_2 knowledge item is rejected
        res_del = voice_client.delete(f"{KNOWLEDGE_URL}/{biz2_item_id}", headers=auth_headers(BIZ_1))
        assert res_del.status_code == 404

    def test_batch_create_knowledge(self, voice_client, voice_business):
        batch = {
            "items": [
                {
                    "category": KnowledgeCategory.PRODUCT_FEATURE,
                    "title": "CRM Sync",
                    "content": "Supports real-time two-way synchronization with HubSpot.",
                    "tags": ["hubspot", "sync"],
                    "source_reference": "Feature Spec",
                },
                {
                    "category": KnowledgeCategory.ELIGIBILITY,
                    "title": "Team Size Requirements",
                    "content": "Requires minimum 3 active sales agents.",
                    "tags": ["eligibility", "team"],
                    "source_reference": "Eligibility Guide",
                },
            ]
        }
        res = voice_client.post(f"{KNOWLEDGE_URL}/batch", json=batch, headers=auth_headers(BIZ_1))
        assert res.status_code == 201
        data = res.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_update_and_delete_knowledge(self, voice_client, seeded_knowledge):
        item_id = seeded_knowledge["biz1_items"][0].id
        update_payload = {"title": "Starter Plan 2026", "priority": 15}
        res_update = voice_client.put(
            f"{KNOWLEDGE_URL}/{item_id}", json=update_payload, headers=auth_headers(BIZ_1)
        )
        assert res_update.status_code == 200
        assert res_update.json()["title"] == "Starter Plan 2026"
        assert res_update.json()["priority"] == 15

        res_del = voice_client.delete(f"{KNOWLEDGE_URL}/{item_id}", headers=auth_headers(BIZ_1))
        assert res_del.status_code == 204

        res_get = voice_client.get(f"{KNOWLEDGE_URL}/{item_id}", headers=auth_headers(BIZ_1))
        assert res_get.status_code == 404


class TestKnowledgeSearch:
    def test_search_knowledge_relevance(self, db, seeded_knowledge):
        results = repo.search_knowledge_items(
            db, business_id=BIZ_1, query="How much does the Starter plan cost?"
        )
        assert len(results) > 0
        top_item, score = results[0]
        assert top_item.title == "Starter Plan"
        assert score > 0.5

    def test_search_category_filter(self, db, seeded_knowledge):
        results = repo.search_knowledge_items(
            db, business_id=BIZ_1, query="refund guarantee", category=KnowledgeCategory.POLICY
        )
        assert len(results) == 1
        assert results[0][0].category == KnowledgeCategory.POLICY

    def test_search_does_not_leak_cross_tenant(self, db, seeded_knowledge):
        # Querying BIZ_1 for "solar" returns empty even though BIZ_2 has solar item
        results = repo.search_knowledge_items(db, business_id=BIZ_1, query="solar package")
        assert len(results) == 0
