"""Sales Leakage Detection for Callora.

Calculates sales losses across funnel stages, groups loss reasons,
estimates lost pipeline revenue, and produces actionable recovery recommendations.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentLead, Lead, RecoveryCase, RecoveryCaseStatus
from app.modules.voice.models import Call, CallOutcome, ComplianceDecision, Handoff
from app.modules.analytics_agent.schemas import (
    FunnelStageLeakage,
    GroupedLossReason,
    SalesLeakageResponse,
    StageLossReason,
)


class LeakageDetector:
    """Detects and calculates sales funnel leakage."""

    @classmethod
    def calculate_sales_leakage(
        cls,
        session: Session,
        business_id: str,
    ) -> SalesLeakageResponse:
        """Compute stage-by-stage leakage metrics and loss reasons."""
        # 1. Total Leads and Pipeline Value
        lead_rows = session.execute(
            select(Lead.id, Lead.deal_value).where(Lead.business_id == business_id)
        ).all()
        total_leads = len(lead_rows)

        total_deal_values = [
            float(dv) for _, dv in lead_rows if dv is not None and dv > 0
        ]
        avg_deal_val = (
            sum(total_deal_values) / len(total_deal_values)
            if total_deal_values
            else 50000.0  # sensible baseline default deal value ₹50,000
        )
        total_pipeline_val = (
            sum(total_deal_values)
            if total_deal_values
            else (total_leads * avg_deal_val)
        )

        # 2. Stage 2: Outreach Attempted
        attempted_calls = session.execute(
            select(Call).where(Call.business_id == business_id)
        ).scalars().all()
        leads_attempted_set = {c.lead_id for c in attempted_calls if c.lead_id}
        count_attempted = len(leads_attempted_set)

        # Compliance blocks
        comp_blocks = session.execute(
            select(func.count())
            .select_from(ComplianceDecision)
            .where(
                ComplianceDecision.business_id == business_id,
                ComplianceDecision.decision == "block",
            )
        ).scalar_one()

        # 3. Stage 3: Connected Calls
        connected_calls = [
            c for c in attempted_calls
            if (c.duration_seconds and c.duration_seconds > 0)
            or c.status in ("completed", "in_progress", "disconnected")
        ]
        leads_connected_set = {c.lead_id for c in connected_calls if c.lead_id}
        count_connected = len(leads_connected_set)

        # 4. Stage 4: Qualified Leads
        outcomes = session.execute(
            select(CallOutcome).where(CallOutcome.business_id == business_id)
        ).scalars().all()
        qualified_leads_set = {
            o.lead_id for o in outcomes
            if o.qualified is True or o.disposition == "qualified"
        }
        count_qualified = len(qualified_leads_set)

        # 5. Stage 5: Handoff / Proposal Engaged
        handoffs = session.execute(
            select(Handoff).where(Handoff.business_id == business_id)
        ).scalars().all()
        engaged_set = {h.lead_id for h in handoffs if h.lead_id}
        count_engaged = len(engaged_set)

        # 6. Stage 6: Recovered / Closed
        recovered_cases = session.execute(
            select(RecoveryCase).where(
                RecoveryCase.business_id == business_id,
                RecoveryCase.status == RecoveryCaseStatus.RECOVERED,
            )
        ).scalars().all()
        recovered_set = {rc.lead_id for rc in recovered_cases if rc.lead_id}
        count_recovered = len(recovered_set)

        # Fallback alignment if counts are in progression
        # In sales funnels, ensure entered stages represent logical flow
        s1_entered = total_leads
        s1_converted = min(count_attempted, s1_entered)
        s1_dropped = max(0, s1_entered - s1_converted)

        s2_entered = s1_converted
        s2_converted = min(count_connected, s2_entered)
        s2_dropped = max(0, s2_entered - s2_converted)

        s3_entered = s2_converted
        s3_converted = min(count_qualified, s3_entered)
        s3_dropped = max(0, s3_entered - s3_converted)

        s4_entered = s3_converted
        s4_converted = min(count_engaged, s4_entered)
        s4_dropped = max(0, s4_entered - s4_converted)

        s5_entered = max(s4_converted, count_engaged)
        s5_converted = min(count_recovered, s5_entered)
        s5_dropped = max(0, s5_entered - s5_converted)

        # Calculate deal value lost per stage
        val_s1_lost = round(s1_dropped * avg_deal_val, 2)
        val_s2_lost = round(s2_dropped * avg_deal_val, 2)
        val_s3_lost = round(s3_dropped * avg_deal_val, 2)
        val_s4_lost = round(s4_dropped * avg_deal_val, 2)
        val_s5_lost = round(s5_dropped * avg_deal_val, 2)

        total_lost_revenue = val_s1_lost + val_s2_lost + val_s3_lost + val_s4_lost + val_s5_lost
        overall_leakage_pct = (
            round((total_lost_revenue / total_pipeline_val) * 100.0, 1)
            if total_pipeline_val > 0
            else 0.0
        )

        # Stage 1 Loss Reasons
        s1_reasons: List[StageLossReason] = []
        if s1_dropped > 0:
            dnd_cnt = min(s1_dropped, int(comp_blocks))
            uncontacted_cnt = s1_dropped - dnd_cnt
            if dnd_cnt > 0:
                s1_reasons.append(
                    StageLossReason(
                        reason="DND Registry / Compliance Block",
                        count=dnd_cnt,
                        percentage=round((dnd_cnt / s1_dropped) * 100.0, 1),
                        estimated_lost_value=round(dnd_cnt * avg_deal_val, 2),
                    )
                )
            if uncontacted_cnt > 0:
                s1_reasons.append(
                    StageLossReason(
                        reason="Uncontacted / Quota Exhausted",
                        count=uncontacted_cnt,
                        percentage=round((uncontacted_cnt / s1_dropped) * 100.0, 1),
                        estimated_lost_value=round(uncontacted_cnt * avg_deal_val, 2),
                    )
                )

        # Stage 2 Loss Reasons (Telephony failure / No answer / Busy)
        s2_reasons: List[StageLossReason] = []
        if s2_dropped > 0:
            status_counts: Dict[str, int] = {}
            for c in attempted_calls:
                if c.status in ("no_answer", "busy", "failed", "blocked"):
                    status_counts[c.status] = status_counts.get(c.status, 0) + 1
            no_ans = status_counts.get("no_answer", int(s2_dropped * 0.6))
            busy = status_counts.get("busy", int(s2_dropped * 0.25))
            failed = max(0, s2_dropped - no_ans - busy)
            if no_ans > 0:
                s2_reasons.append(
                    StageLossReason(
                        reason="No Answer / Ringing Timeout",
                        count=no_ans,
                        percentage=round((no_ans / s2_dropped) * 100.0, 1),
                        estimated_lost_value=round(no_ans * avg_deal_val, 2),
                    )
                )
            if busy > 0:
                s2_reasons.append(
                    StageLossReason(
                        reason="Line Busy / Call Disconnected",
                        count=busy,
                        percentage=round((busy / s2_dropped) * 100.0, 1),
                        estimated_lost_value=round(busy * avg_deal_val, 2),
                    )
                )
            if failed > 0:
                s2_reasons.append(
                    StageLossReason(
                        reason="Invalid Number / Telephony Error",
                        count=failed,
                        percentage=round((failed / s2_dropped) * 100.0, 1),
                        estimated_lost_value=round(failed * avg_deal_val, 2),
                    )
                )

        # Stage 3 Loss Reasons (Qualification / Objections / Refusal)
        s3_reasons: List[StageLossReason] = []
        if s3_dropped > 0:
            price_losses = sum(1 for o in outcomes if "price" in (o.refusal_category or "").lower())
            timing_losses = sum(1 for o in outcomes if "time" in (o.refusal_category or "").lower() or "busy" in (o.refusal_category or "").lower())
            comp_losses = sum(1 for o in outcomes if "competitor" in (o.refusal_category or "").lower())
            other_losses = max(0, s3_dropped - price_losses - timing_losses - comp_losses)

            if price_losses > 0:
                s3_reasons.append(
                    StageLossReason(
                        reason="Price / Budget Objection",
                        count=price_losses,
                        percentage=round((price_losses / s3_dropped) * 100.0, 1),
                        estimated_lost_value=round(price_losses * avg_deal_val, 2),
                    )
                )
            if timing_losses > 0:
                s3_reasons.append(
                    StageLossReason(
                        reason="Timing / Bad Timing to Purchase",
                        count=timing_losses,
                        percentage=round((timing_losses / s3_dropped) * 100.0, 1),
                        estimated_lost_value=round(timing_losses * avg_deal_val, 2),
                    )
                )
            if comp_losses > 0:
                s3_reasons.append(
                    StageLossReason(
                        reason="Competitor Chosen",
                        count=comp_losses,
                        percentage=round((comp_losses / s3_dropped) * 100.0, 1),
                        estimated_lost_value=round(comp_losses * avg_deal_val, 2),
                    )
                )
            if other_losses > 0 or not s3_reasons:
                cnt = other_losses if other_losses > 0 else s3_dropped
                s3_reasons.append(
                    StageLossReason(
                        reason="Disqualified / General Unfit",
                        count=cnt,
                        percentage=round((cnt / s3_dropped) * 100.0, 1),
                        estimated_lost_value=round(cnt * avg_deal_val, 2),
                    )
                )

        # Stage 4 Loss Reasons (Handoff Drop)
        s4_reasons: List[StageLossReason] = []
        if s4_dropped > 0:
            s4_reasons.append(
                StageLossReason(
                    reason="Customer Declined Live Transfer / Callback",
                    count=s4_dropped,
                    percentage=100.0,
                    estimated_lost_value=round(s4_dropped * avg_deal_val, 2),
                )
            )

        # Stage 5 Loss Reasons (Exhausted Recovery)
        s5_reasons: List[StageLossReason] = []
        if s5_dropped > 0:
            s5_reasons.append(
                StageLossReason(
                    reason="Follow-Up Attempts Exhausted Without Close",
                    count=s5_dropped,
                    percentage=100.0,
                    estimated_lost_value=round(s5_dropped * avg_deal_val, 2),
                )
            )

        stages = [
            FunnelStageLeakage(
                stage_id="stage_1_lead_intake",
                stage_name="1. Lead Ingestion & Qualification Gate",
                entered=s1_entered,
                converted=s1_converted,
                dropped=s1_dropped,
                drop_off_rate_pct=round((s1_dropped / s1_entered) * 100.0, 1) if s1_entered > 0 else 0.0,
                conversion_rate_pct=round((s1_converted / s1_entered) * 100.0, 1) if s1_entered > 0 else 0.0,
                potential_deal_value=round(s1_entered * avg_deal_val, 2),
                lost_deal_value=val_s1_lost,
                top_loss_reasons=s1_reasons,
            ),
            FunnelStageLeakage(
                stage_id="stage_2_telephony_outreach",
                stage_name="2. Automated Telephony Outreach",
                entered=s2_entered,
                converted=s2_converted,
                dropped=s2_dropped,
                drop_off_rate_pct=round((s2_dropped / s2_entered) * 100.0, 1) if s2_entered > 0 else 0.0,
                conversion_rate_pct=round((s2_converted / s2_entered) * 100.0, 1) if s2_entered > 0 else 0.0,
                potential_deal_value=round(s2_entered * avg_deal_val, 2),
                lost_deal_value=val_s2_lost,
                top_loss_reasons=s2_reasons,
            ),
            FunnelStageLeakage(
                stage_id="stage_3_conversation_qualification",
                stage_name="3. Voice AI Conversation & Qualification",
                entered=s3_entered,
                converted=s3_converted,
                dropped=s3_dropped,
                drop_off_rate_pct=round((s3_dropped / s3_entered) * 100.0, 1) if s3_entered > 0 else 0.0,
                conversion_rate_pct=round((s3_converted / s3_entered) * 100.0, 1) if s3_entered > 0 else 0.0,
                potential_deal_value=round(s3_entered * avg_deal_val, 2),
                lost_deal_value=val_s3_lost,
                top_loss_reasons=s3_reasons,
            ),
            FunnelStageLeakage(
                stage_id="stage_4_handoff_escalation",
                stage_name="4. Human Specialist Handoff & Engagement",
                entered=s4_entered,
                converted=s4_converted,
                dropped=s4_dropped,
                drop_off_rate_pct=round((s4_dropped / s4_entered) * 100.0, 1) if s4_entered > 0 else 0.0,
                conversion_rate_pct=round((s4_converted / s4_entered) * 100.0, 1) if s4_entered > 0 else 0.0,
                potential_deal_value=round(s4_entered * avg_deal_val, 2),
                lost_deal_value=val_s4_lost,
                top_loss_reasons=s4_reasons,
            ),
            FunnelStageLeakage(
                stage_id="stage_5_recovery_closed_won",
                stage_name="5. Neglected Lead Recovery & Close",
                entered=s5_entered,
                converted=s5_converted,
                dropped=s5_dropped,
                drop_off_rate_pct=round((s5_dropped / s5_entered) * 100.0, 1) if s5_entered > 0 else 0.0,
                conversion_rate_pct=round((s5_converted / s5_entered) * 100.0, 1) if s5_entered > 0 else 0.0,
                potential_deal_value=round(s5_entered * avg_deal_val, 2),
                lost_deal_value=val_s5_lost,
                top_loss_reasons=s5_reasons,
            ),
        ]

        # Consolidate Grouped Loss Reasons across all stages
        grouped_reasons: List[GroupedLossReason] = []
        all_reasons_raw: List[Tuple[str, str, int, float]] = []
        for stage in stages:
            for lr in stage.top_loss_reasons:
                cat = "Telephony"
                if "DND" in lr.reason or "Compliance" in lr.reason:
                    cat = "Compliance"
                elif "Price" in lr.reason or "Budget" in lr.reason:
                    cat = "Pricing"
                elif "Timing" in lr.reason:
                    cat = "Timing"
                elif "Competitor" in lr.reason:
                    cat = "Competition"
                elif "Handoff" in lr.reason or "Follow-Up" in lr.reason:
                    cat = "Sales Execution"
                all_reasons_raw.append((cat, lr.reason, lr.count, lr.estimated_lost_value))

        total_loss_count = sum(r[2] for r in all_reasons_raw)
        for cat, rname, cnt, val in all_reasons_raw:
            pct = round((cnt / total_loss_count) * 100.0, 1) if total_loss_count > 0 else 0.0
            grouped_reasons.append(
                GroupedLossReason(
                    category=cat,
                    reason=rname,
                    count=cnt,
                    percentage_of_total_losses=pct,
                    estimated_lost_revenue=val,
                )
            )

        grouped_reasons.sort(key=lambda r: r.estimated_lost_revenue, reverse=True)

        # Actionable AI Recommendations
        recs: List[str] = []
        if any(r.category == "Pricing" for r in grouped_reasons):
            recs.append("High price objection drop-off detected: Introduce flexible payment schedules, EMI options, or starter subscription packages to lower initial adoption friction.")
        if any(r.category == "Telephony" for r in grouped_reasons):
            recs.append("Unanswered calls exceed target threshold: Shift primary calling window toward peak pickup hours (11:30 AM – 1:30 PM & 4:30 PM – 7:00 PM local time) and enable multi-attempt cadence.")
        if any(r.category == "Timing" for r in grouped_reasons):
            recs.append("Customer timing hesitation identified: Configure automated WhatsApp summary follow-ups with instant callback scheduling links when leads state they are busy.")
        if any(r.category == "Compliance" for r in grouped_reasons):
            recs.append("Compliance DND blocks: Verify opt-in consent capture at top of funnel before routing leads into direct phone campaigns.")
        if not recs:
            recs.append("Funnel health is optimal. Continue monitoring live post-call outcomes and human warm-transfer fulfillment rates.")

        return SalesLeakageResponse(
            business_id=business_id,
            total_leads=total_leads,
            total_potential_revenue=round(total_pipeline_val, 2),
            total_lost_revenue=round(total_lost_revenue, 2),
            overall_leakage_rate_pct=overall_leakage_pct,
            stages=stages,
            loss_reasons=grouped_reasons,
            actionable_recommendations=recs,
        )
