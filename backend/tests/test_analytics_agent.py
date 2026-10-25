"""Tests for Callora Analytics Agent.

Covers:
- GET /analytics/summary (summary metrics, funnel counts, KPIs, cost/revenue)
- GET /analytics/leakage (funnel stage losses, grouped reasons, recommendations)
- GET /analytics/conversations (intent extraction, objections, sentiment, grouped refusals, per-call insights)
- Empty-data handling (zero division guards, safe defaults)
- Calculations with realistic stored records
- Filtering and pagination
- Dashboard UI route (HTML response with KPI cards and charts)
- Cross-tenant business isolation
- Root (/analytics/...) and API_PREFIX (/api/v1/analytics/...) routing parity
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from app.models import AgentLead, Lead, RecoveryCase, RecoveryCaseStatus
from app.modules.voice.models import Business, Call, CallOutcome, ComplianceDecision, Handoff, VoiceCallMemory
from tests.conftest import BIZ_1, BIZ_2, KEY_1, KEY_2, auth_headers


@pytest.fixture()
def populated_analytics_data(db, voice_business):
    """Seed a realistic set of leads, calls, outcomes, handoffs, and recovery cases for BIZ_1."""
    # 1. Leads
    l1 = Lead(
        id="lead_a1",
        business_id=BIZ_1,
        name="Ananya Verma",
        phone="+919876543201",
        source="website",
        deal_value=Decimal("150000.00"),
    )
    l2 = Lead(
        id="lead_a2",
        business_id=BIZ_1,
        name="Vikram Sethi",
        phone="+919876543202",
        source="inbound_form",
        deal_value=Decimal("80000.00"),
    )
    l3 = Lead(
        id="lead_a3",
        business_id=BIZ_1,
        name="Pooja Rao",
        phone="+919876543203",
        source="campaign",
        deal_value=Decimal("120000.00"),
    )
    l4 = Lead(
        id="lead_a4",
        business_id=BIZ_1,
        name="Rohan Iyer",
        phone="+919876543204",
        source="referral",
        deal_value=Decimal("200000.00"),
    )
    db.add_all([l1, l2, l3, l4])

    # 2. Calls
    # Call 1: Connected, Qualified, High Sentiment
    c1 = Call(
        id="call_a1",
        business_id=BIZ_1,
        lead_id=l1.id,
        status="completed",
        duration_seconds=180,
        cost=Decimal("12.50"),
        summary="Customer Rahul is very interested in 3BHK flat and wants to schedule a site visit.",
        transcript="Agent: Hello Rahul. Lead: Yes, I am interested, sounds good, please schedule a visit.",
    )
    # Call 2: Connected, Price Refusal, Negative Sentiment
    c2 = Call(
        id="call_a2",
        business_id=BIZ_1,
        lead_id=l2.id,
        status="completed",
        duration_seconds=95,
        cost=Decimal("7.00"),
        summary="Customer stated the price is too high and out of budget.",
        transcript="Lead: Your price is expensive and outside my budget. Do not call again.",
    )
    # Call 3: Connected, Timing objection, Callback Requested
    c3 = Call(
        id="call_a3",
        business_id=BIZ_1,
        lead_id=l3.id,
        status="completed",
        duration_seconds=120,
        cost=Decimal("9.20"),
        summary="Customer is busy driving and requested a callback tomorrow afternoon.",
        transcript="Lead: I am driving right now. Please call me back tomorrow.",
    )
    # Call 4: Unanswered / No Answer
    c4 = Call(
        id="call_a4",
        business_id=BIZ_1,
        lead_id=l4.id,
        status="no_answer",
        duration_seconds=0,
        cost=Decimal("1.50"),
        summary="Call timed out with no answer from lead.",
    )
    db.add_all([c1, c2, c3, c4])

    # 3. Call Outcomes
    o1 = CallOutcome(
        id="out_a1",
        business_id=BIZ_1,
        call_id=c1.id,
        lead_id=l1.id,
        summary="Lead qualified for Bangalore 3BHK luxury flat.",
        disposition="qualified",
        qualified=True,
        qualification_score=90.0,
        follow_up_actions=[{"action": "send_brochure_whatsapp"}, {"action": "confirm_site_visit"}],
        handoff_required=True,
    )
    o2 = CallOutcome(
        id="out_a2",
        business_id=BIZ_1,
        call_id=c2.id,
        lead_id=l2.id,
        summary="Customer declined due to budget limitation.",
        disposition="refused",
        qualified=False,
        refusal_category="pricing",
        refusal_reason="Too expensive for current quarterly budget",
    )
    o3 = CallOutcome(
        id="out_a3",
        business_id=BIZ_1,
        call_id=c3.id,
        lead_id=l3.id,
        summary="Lead requested phone callback.",
        disposition="callback_requested",
        qualified=None,
        refusal_category="timing",
        refusal_reason="Customer busy in meeting/driving",
        follow_up_actions=[{"action": "callback_tomorrow"}],
    )
    db.add_all([o1, o2, o3])

    # 4. Handoff
    h1 = Handoff(
        id="ho_a1",
        business_id=BIZ_1,
        call_id=c1.id,
        lead_id=l1.id,
        kind="warm_transfer",
        status="completed",
    )
    h2 = Handoff(
        id="ho_a2",
        business_id=BIZ_1,
        call_id=c3.id,
        lead_id=l3.id,
        kind="callback",
        status="requested",
    )
    db.add_all([h1, h2])

    # 5. Recovery Case (Recovered)
    rc1 = RecoveryCase(
        id="case_a1",
        business_id=BIZ_1,
        lead_id=l1.id,
        status=RecoveryCaseStatus.RECOVERED,
        priority_level="critical",
        score=Decimal("0.95"),
    )
    rc2 = RecoveryCase(
        id="case_a2",
        business_id=BIZ_1,
        lead_id=l2.id,
        status=RecoveryCaseStatus.IN_RECOVERY,
        priority_level="high",
        score=Decimal("0.75"),
    )
    db.add_all([rc1, rc2])

    # 6. Compliance Decision
    comp = ComplianceDecision(
        id="cmp_a1",
        business_id=BIZ_1,
        lead_id=l2.id,
        decision="block",
        blocked_reasons=["dnd_registered"],
    )
    db.add(comp)

    # 7. Add BIZ_2 data to test tenant isolation
    biz2 = Business(
        id=BIZ_2,
        name="Biz Two Realty",
        timezone="Asia/Kolkata",
        default_language="en",
        is_active=True,
    )
    l_biz2 = Lead(
        id="lead_b1",
        business_id=BIZ_2,
        name="Other Company Lead",
        phone="+919876543299",
        source="other",
        deal_value=Decimal("500000.00"),
    )
    c_biz2 = Call(
        id="call_b1",
        business_id=BIZ_2,
        lead_id=l_biz2.id,
        status="completed",
        duration_seconds=300,
        cost=Decimal("25.00"),
    )
    db.add_all([biz2, l_biz2, c_biz2])

    db.commit()


# ===========================================================================
# 1. EMPTY DATABASE / SAFE ZERO HANDLING TESTS
# ===========================================================================


class TestAnalyticsEmptyData:
    def test_summary_empty_database(self, client, voice_business):
        """GET /analytics/summary returns safe 0s without errors on empty DB."""
        res = client.get("/analytics/summary", headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        data = res.json()
        assert data["business_id"] == BIZ_1
        assert data["lead_funnel"]["total_leads"] == 0
        assert data["financials"]["total_call_cost"] == 0.0
        assert data["financials"]["recorded_revenue_recovered"] == 0.0
        assert data["financials"]["cost_per_recovered_opportunity"] == 0.0
        assert data["financials"]["roi_multiple"] == 0.0
        assert data["rates_and_kpis"]["connection_rate_pct"] == 0.0
        assert data["rates_and_kpis"]["qualification_rate_pct"] == 0.0
        assert data["recovery_metrics"]["recovery_rate_pct"] == 0.0

    def test_leakage_empty_database(self, client, voice_business):
        """GET /analytics/leakage handles zero leads safely."""
        res = client.get("/analytics/leakage", headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        data = res.json()
        assert data["business_id"] == BIZ_1
        assert data["total_leads"] == 0
        assert data["total_potential_revenue"] == 0.0
        assert data["total_lost_revenue"] == 0.0
        assert data["overall_leakage_rate_pct"] == 0.0
        assert len(data["stages"]) == 5
        assert all(s["entered"] == 0 and s["dropped"] == 0 for s in data["stages"])
        assert isinstance(data["actionable_recommendations"], list)

    def test_conversations_empty_database(self, client, voice_business):
        """GET /analytics/conversations handles zero calls safely."""
        res = client.get("/analytics/conversations", headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        data = res.json()
        assert data["business_id"] == BIZ_1
        assert data["total_conversations"] == 0
        assert data["call_insights"] == []
        assert data["sentiment_breakdown"]["positive"] == 0
        assert data["sentiment_breakdown"]["neutral"] == 0
        assert data["sentiment_breakdown"]["negative"] == 0
        assert data["common_objections"] == []
        assert data["grouped_refusal_reasons"] == []


# ===========================================================================
# 2. POPULATED ANALYTICS CALCULATIONS TESTS
# ===========================================================================


class TestAnalyticsCalculations:
    def test_summary_metrics_calculated(self, client, populated_analytics_data):
        """Validate accurate calculation of summary metrics, financial ROI, and KPIs."""
        res = client.get("/analytics/summary", headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        data = res.json()

        assert data["business_id"] == BIZ_1

        # Funnel counts
        funnel = data["lead_funnel"]
        assert funnel["total_leads"] == 4
        assert funnel["contact_attempted"] >= 1
        assert funnel["calls_connected"] == 3  # c1, c2, c3
        assert funnel["leads_qualified"] == 1  # l1
        assert funnel["recovered_opportunities"] == 1  # rc1

        # Financials
        fin = data["financials"]
        # Total call costs = 12.50 + 7.00 + 9.20 + 1.50 = 30.20
        assert fin["total_call_cost"] == 30.20
        assert fin["currency"] == "INR"
        # Revenue recovered for l1 = 150000.00
        assert fin["recorded_revenue_recovered"] == 150000.00
        assert fin["cost_per_recovered_opportunity"] == 30.20
        assert fin["roi_multiple"] > 1000.0  # 150,000 / 30.20 ~ 4966.89x
        assert fin["net_revenue_recovered"] == round(150000.00 - 30.20, 2)

        # Performance KPIs
        kpis = data["rates_and_kpis"]
        assert kpis["connection_rate_pct"] == 75.0  # 3 of 4 calls connected
        assert kpis["qualification_rate_pct"] == 33.3  # 1 of 3 connected qualified
        assert kpis["warm_transfers_completed"] == 1
        assert kpis["follow_ups_scheduled"] == 3  # 2 on o1 + 1 on o3
        assert kpis["callbacks_requested"] >= 1

        # Recovery Metrics
        rec = data["recovery_metrics"]
        assert rec["total_eligible_cases"] == 2
        assert rec["recovered_count"] == 1
        assert rec["in_recovery"] == 1
        assert rec["recovery_rate_pct"] == 50.0  # 1 of 2 cases recovered
        assert "critical" in rec["priority_breakdown"]
        assert rec["priority_breakdown"]["critical"]["recovered"] == 1

    def test_sales_leakage_stages_and_reasons(self, client, populated_analytics_data):
        """Validate stage-by-stage sales leakage calculation and loss reasons."""
        res = client.get("/analytics/leakage", headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        data = res.json()

        assert data["business_id"] == BIZ_1
        assert data["total_leads"] == 4
        assert data["total_potential_revenue"] > 0
        assert data["total_lost_revenue"] >= 0

        stages = data["stages"]
        assert len(stages) == 5
        # Stage 2: Telephony outreach
        s2 = stages[1]
        assert s2["stage_id"] == "stage_2_telephony_outreach"
        assert s2["entered"] == 4
        assert s2["converted"] == 3
        assert s2["dropped"] == 1  # call_a4 no answer

        # Loss reasons check
        loss_reasons = data["loss_reasons"]
        assert len(loss_reasons) > 0
        reason_names = [r["reason"] for r in loss_reasons]
        # Should detect no-answer, pricing, or compliance reasons
        assert any("Answer" in r or "Price" in r or "Budget" in r for r in reason_names)

        # Actionable recommendations
        recs = data["actionable_recommendations"]
        assert len(recs) > 0

    def test_conversations_analytics_and_insights(self, client, populated_analytics_data):
        """Validate conversation analytics, sentiment distribution, and per-call insights."""
        res = client.get("/analytics/conversations", headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        data = res.json()

        assert data["business_id"] == BIZ_1
        assert data["total_conversations"] == 4

        # Sentiment breakdown
        sent = data["sentiment_breakdown"]
        assert sent["positive"] >= 1  # call_a1
        assert sent["negative"] >= 1  # call_a2
        assert (sent["positive"] + sent["neutral"] + sent["negative"]) == 4

        # Grouped refusal reasons
        refusals = data["grouped_refusal_reasons"]
        assert len(refusals) > 0
        refusal_categories = [r["refusal_category"] for r in refusals]
        assert "Pricing & Budget" in refusal_categories

        # Common objections
        objections = [o["objection"] for o in data["common_objections"]]
        assert any("Price" in o or "Timing" in o for o in objections)

        # Per-call insights
        insights = data["call_insights"]
        assert len(insights) == 4
        call1_insight = next(c for c in insights if c["call_id"] == "call_a1")
        assert call1_insight["qualified"] is True
        assert call1_insight["sentiment"] == "positive"
        assert call1_insight["handoff_status"] == "completed"

        call2_insight = next(c for c in insights if c["call_id"] == "call_a2")
        assert call2_insight["qualified"] is False
        assert call2_insight["sentiment"] == "negative"
        assert "pric" in (call2_insight["refusal_category"] or "").lower()


# ===========================================================================
# 3. FILTERING, PAGINATION, AND TENANT ISOLATION TESTS
# ===========================================================================


class TestAnalyticsFiltersAndIsolation:
    def test_conversations_sentiment_filter(self, client, populated_analytics_data):
        """Filtering by sentiment returns only matching call insights."""
        res_pos = client.get("/analytics/conversations?sentiment=positive", headers=auth_headers(BIZ_1))
        assert res_pos.status_code == 200
        data_pos = res_pos.json()
        assert all(c["sentiment"] == "positive" for c in data_pos["call_insights"])

        res_neg = client.get("/analytics/conversations?sentiment=negative", headers=auth_headers(BIZ_1))
        assert res_neg.status_code == 200
        data_neg = res_neg.json()
        assert all(c["sentiment"] == "negative" for c in data_neg["call_insights"])

    def test_conversations_pagination(self, client, populated_analytics_data):
        """Limit and offset paginate call insights accurately."""
        res = client.get("/analytics/conversations?limit=2&offset=0", headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        data = res.json()
        assert len(data["call_insights"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0
        assert data["total"] == 4

        res_p2 = client.get("/analytics/conversations?limit=2&offset=2", headers=auth_headers(BIZ_1))
        assert res_p2.status_code == 200
        data_p2 = res_p2.json()
        assert len(data_p2["call_insights"]) == 2
        # Disjoint call IDs
        ids_p1 = {c["call_id"] for c in data["call_insights"]}
        ids_p2 = {c["call_id"] for c in data_p2["call_insights"]}
        assert ids_p1.isdisjoint(ids_p2)

    def test_cross_tenant_isolation(self, client, populated_analytics_data):
        """BIZ_1 cannot see BIZ_2 data, and BIZ_2 only sees its own records."""
        # Query BIZ_1
        res1 = client.get("/analytics/summary", headers=auth_headers(BIZ_1))
        data1 = res1.json()
        # Query BIZ_2
        res2 = client.get("/analytics/summary", headers=auth_headers(BIZ_2))
        data2 = res2.json()

        assert data1["business_id"] == BIZ_1
        assert data2["business_id"] == BIZ_2
        # BIZ_2 only has 1 lead and 1 call
        assert data2["lead_funnel"]["total_leads"] == 1
        assert data2["financials"]["total_call_cost"] == 25.00


# ===========================================================================
# 4. DASHBOARD UI AND ROUTE PARITY TESTS
# ===========================================================================


class TestAnalyticsDashboardAndRouting:
    def test_analytics_dashboard_html_response(self, client, populated_analytics_data):
        """GET /analytics/dashboard serves HTML with KPI cards and charts."""
        res = client.get("/analytics/dashboard", headers=auth_headers(BIZ_1))
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        body = res.text
        assert "Callora Analytics Agent" in body
        assert "Sales Funnel Conversion" in body
        assert "Primary Objection & Loss Reasons" in body
        assert "kpi-recovered-revenue" in body
        assert "kpi-leakage-value" in body

    def test_top_level_dashboard_route(self, client, populated_analytics_data):
        """GET /dashboard serves the analytics dashboard UI."""
        res = client.get("/dashboard")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "Callora Analytics Agent" in res.text

    def test_api_prefix_parity(self, client, populated_analytics_data):
        """Endpoints are accessible at both /analytics/... and /api/v1/analytics/..."""
        res_root = client.get("/analytics/summary", headers=auth_headers(BIZ_1))
        res_v1 = client.get("/api/v1/analytics/summary", headers=auth_headers(BIZ_1))
        assert res_root.status_code == 200
        assert res_v1.status_code == 200
        assert res_root.json() == res_v1.json()

    def test_unauthenticated_query_fallback(self, client, populated_analytics_data):
        """Requests without X-API-Key succeed using query param or default business."""
        res = client.get(f"/analytics/summary?business_id={BIZ_1}")
        assert res.status_code == 200
        assert res.json()["business_id"] == BIZ_1
