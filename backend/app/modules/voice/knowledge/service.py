"""Follow-up Question Service.

Coordinates:
- Business and call context validation (cross-business isolation)
- Conversation memory tracking
- Customer refusal and immediate opt-out enforcement
- Human handoff and callback integration
- Ambiguity detection and natural clarification
- Grounded business knowledge retrieval and generation
- Sales qualification flow continuation
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import Settings
from app.modules.voice import repository as repo
from app.modules.voice.constants import (
    AuditAction,
    CallStatus,
    FollowUpAction,
    HandoffKind,
    HandoffStatus,
)
from app.modules.voice.errors import (
    BusinessNotFoundError,
    CallNotFoundError,
    CrossBusinessAccessError,
    InvalidCallContextError,
)
from app.modules.voice.handoff.service import HandoffService
from app.modules.voice.knowledge.guardrails import AIGuardrails
from app.modules.voice.knowledge.retriever import KnowledgeRetriever
from app.modules.voice.models import Business, Call, VoiceCallMemory, utcnow
from app.modules.voice.postcall.llm import LLMClient, NullLLMClient, build_llm_client
from app.modules.voice.schemas import (
    FollowUpQuestionRequest,
    FollowUpQuestionResponse,
)

logger = logging.getLogger("callora.voice.knowledge.service")

# Statuses that are considered active or valid call contexts for answering questions
VALID_CALL_STATUSES = frozenset(
    {
        CallStatus.QUEUED,
        CallStatus.INITIATED,
        CallStatus.RINGING,
        CallStatus.IN_PROGRESS,
        CallStatus.SIMULATED,
        CallStatus.COMPLETED,
        CallStatus.PENDING,
        CallStatus.SCHEDULED,
        CallStatus.DISCONNECTED,
    }
)


class FollowUpService:
    """Answers customer follow-up questions during live calls."""

    def __init__(
        self,
        settings: Settings,
        session: Session,
        llm_client: Optional[LLMClient] = None,
        handoff_service: Optional[HandoffService] = None,
    ) -> None:
        self._settings = settings
        self._session = session
        self._llm = llm_client or build_llm_client(settings)
        self._handoff_service = handoff_service or HandoffService(settings, session)
        self._retriever = KnowledgeRetriever(session)

    def answer_question(self, request: FollowUpQuestionRequest) -> FollowUpQuestionResponse:
        """Validate context, retrieve verified facts, evaluate guardrails, and generate response."""
        business, call = self._validate_context(
            request.business_id, request.call_id, getattr(request, "execution_id", None)
        )
        memory = self._load_or_init_memory(business, call, request)

        question = (request.question or getattr(request, "query", None) or getattr(request, "text", None) or "").strip()

        # 1. Immediate Opt-Out & Refusal Guardrail
        if AIGuardrails.is_opt_out(question):
            return self._handle_opt_out(business, call, memory, question)

        # 2. Human Escalation Request
        if AIGuardrails.is_human_escalation_request(question):
            return self._handle_human_escalation(business, call, memory, question)

        # 3. Unsupported / Contract Negotiation Guardrail
        if AIGuardrails.is_unsupported_domain_request(question):
            return self._handle_unsupported_question(business, call, memory, question, [])

        # 4. Ambiguity Guardrail
        is_ambig, clarification_msg = AIGuardrails.is_ambiguous(question)
        if is_ambig and clarification_msg:
            return self._handle_clarification(business, call, memory, question, clarification_msg)

        # 4. Knowledge Retrieval
        retrieval = self._retriever.retrieve(
            business_id=business.id,
            question=question,
        )

        # 5. Unsupported / Missing Knowledge Fallback
        if not retrieval.has_knowledge:
            return self._handle_unsupported_question(business, call, memory, question, retrieval.sources)

        # 6. Grounded Answer Generation
        answer, confidence = self._generate_grounded_answer(business, question, retrieval)

        # 7. Qualification Continuation
        continuation_prompt = self._resolve_next_qualification_question(business, memory)
        full_answer = answer
        if continuation_prompt:
            full_answer = f"{answer} {continuation_prompt}"

        # 8. Update Memory & Audit Trail
        memory_record = {
            "question": question,
            "answer": full_answer,
            "sources": retrieval.sources,
            "timestamp": utcnow().isoformat(),
        }
        questions_answered = list(memory.questions_answered or [])
        questions_answered.append(memory_record)

        repo.update_call_memory(
            self._session,
            memory,
            {
                "questions_answered": questions_answered,
                "last_customer_question": question,
                "last_agent_answer": full_answer,
                "customer_intent": "inquire_business_knowledge",
            },
        )

        self._sync_call_metadata(call, memory)

        repo.add_audit_log(
            self._session,
            action=AuditAction.FOLLOWUP_ANSWERED,
            business_id=business.id,
            actor="ai_voice_followup",
            entity_type="call",
            entity_id=call.id,
            decision="allow",
            reason_code="grounded_answer",
            reason="Follow-up question answered using verified business knowledge",
            metadata={
                "question": question,
                "sources": retrieval.sources,
                "confidence": confidence,
            },
        )
        self._session.commit()

        return FollowUpQuestionResponse(
            answer=full_answer,
            source_references=retrieval.sources,
            should_handoff=False,
            handoff_recommended=False,
            handoff_reason=None,
            handoff_type=None,
            action=FollowUpAction.ANSWER,
            confidence=confidence,
            qualification_continuation=continuation_prompt,
            memory_state=self._memory_summary(memory),
        )

    # -- Internal Handlers --------------------------------------------------

    def _validate_context(
        self,
        business_id: Optional[str],
        call_id: Optional[str],
        execution_id: Optional[str] = None,
    ) -> Tuple[Business, Call]:
        call = None
        if call_id and business_id:
            call = repo.get_call(self._session, business_id, call_id)
        elif call_id:
            call = self._session.query(Call).filter(Call.id == call_id).first()
            if call:
                business_id = call.business_id
        elif execution_id:
            call = repo.get_call_by_provider_id(self._session, "bolna", execution_id)
            if call is None:
                call = self._session.query(Call).filter(Call.provider_call_id == str(execution_id)).first()
            if call:
                call_id = call.id
                business_id = call.business_id

        if not business_id:
            raise BusinessNotFoundError("unknown_business")

        business = repo.get_business(self._session, business_id)
        if business is None:
            raise BusinessNotFoundError(business_id)

        if call is None:
            if call_id:
                other_call = self._session.query(Call).filter(Call.id == call_id).first()
                if other_call is not None and other_call.business_id != business_id:
                    raise CrossBusinessAccessError(
                        f"Call '{call_id}' belongs to another business; cross-business access is forbidden"
                    )
                raise CallNotFoundError(call_id)
            raise CallNotFoundError(execution_id or "missing_call_id")

        if call.business_id != business_id:
            raise CrossBusinessAccessError(
                f"Call '{call.id}' belongs to another business; cross-business access is forbidden"
            )

        if call.status == CallStatus.BLOCKED:
            raise InvalidCallContextError(
                f"Call '{call.id}' was blocked by compliance and cannot accept follow-up questions"
            )

        if call.status not in VALID_CALL_STATUSES:
            raise InvalidCallContextError(
                f"Call '{call.id}' is in terminal status '{call.status}'"
            )

        return business, call

    def _load_or_init_memory(
        self, business: Business, call: Call, request: FollowUpQuestionRequest
    ) -> VoiceCallMemory:
        memory = repo.get_or_create_call_memory(
            self._session,
            business_id=business.id,
            call_id=call.id,
            lead_id=call.lead_id,
            language=request.language or business.default_language or "en",
        )

        updates: Dict[str, Any] = {"turns_count": memory.turns_count + 1}

        # Ingest external context fields if supplied
        if request.context:
            qualification_state = request.context.get("qualification_state") or request.context.get(
                "qualification_fields"
            )
            if isinstance(qualification_state, dict):
                current_fields = dict(memory.qualification_fields or {})
                current_fields.update(qualification_state)
                updates["qualification_fields"] = current_fields

                # Remove answered keys from pending
                pending = [
                    k for k in (memory.pending_qualification_keys or []) if k not in current_fields
                ]
                updates["pending_qualification_keys"] = pending

            intent = request.context.get("intent")
            if intent:
                updates["customer_intent"] = str(intent)

        memory = repo.update_call_memory(self._session, memory, updates)
        return memory

    def _handle_opt_out(
        self, business: Business, call: Call, memory: VoiceCallMemory, question: str
    ) -> FollowUpQuestionResponse:
        """Immediately respect customer refusals and mark DND."""
        if call.lead_id:
            repo.upsert_lead_profile(
                self._session, business.id, call.lead_id, {"do_not_call": True}
            )

        phone = call.to_number or ""
        if phone:
            repo.add_dnd_entry(
                self._session,
                business_id=business.id,
                phone=phone,
                scope="business",
                reason="customer_opt_out_during_call",
                source="ai_voice_followup",
            )

        answer = (
            "I completely understand and respect your decision. I have removed your number "
            "from our contact list and we will not call you again. Thank you, and have a good day."
        )

        questions_answered = list(memory.questions_answered or [])
        questions_answered.append(
            {
                "question": question,
                "answer": answer,
                "sources": ["Customer Opt-Out Policy"],
                "action": FollowUpAction.OPT_OUT,
                "timestamp": utcnow().isoformat(),
            }
        )

        repo.update_call_memory(
            self._session,
            memory,
            {
                "opt_out_detected": True,
                "customer_intent": "opt_out",
                "questions_answered": questions_answered,
                "last_customer_question": question,
                "last_agent_answer": answer,
            },
        )
        self._sync_call_metadata(call, memory)

        repo.add_audit_log(
            self._session,
            action=AuditAction.CUSTOMER_OPT_OUT,
            business_id=business.id,
            actor="customer",
            entity_type="call",
            entity_id=call.id,
            decision="allow",
            reason_code="opt_out_requested",
            reason="Customer requested to opt out during live call; marked DND and do_not_call",
            metadata={"question": question, "phone": phone},
        )
        self._session.commit()

        return FollowUpQuestionResponse(
            answer=answer,
            source_references=["Customer Opt-Out Policy"],
            should_handoff=False,
            handoff_recommended=False,
            handoff_reason=None,
            handoff_type=None,
            action=FollowUpAction.OPT_OUT,
            confidence=1.0,
            memory_state=self._memory_summary(memory),
        )

    def _handle_human_escalation(
        self, business: Business, call: Call, memory: VoiceCallMemory, question: str
    ) -> FollowUpQuestionResponse:
        """Execute configured transfer or callback when human requested."""
        if business.warm_transfer_enabled and (
            business.warm_transfer_number or business.handoff_target_number
        ):
            target = business.warm_transfer_number or business.handoff_target_number
            handoff = self._handoff_service.record_warm_transfer(
                call=call,
                reason=question,
                summary="Customer explicitly requested live human transfer during conversation",
                transferred_to=target,
            )
            handoff_type = HandoffKind.WARM_TRANSFER
            answer = "I would be happy to connect you with our specialist. Please hold for just a moment while I transfer your call."
        else:
            handoff = self._handoff_service.request_callback(
                business_id=business.id,
                lead_id=call.lead_id,
                call_id=call.id,
                reason=question,
                briefing="Customer requested assistance; live transfer unavailable, scheduled callback",
                requested_by="ai",
            )
            handoff_type = HandoffKind.CALLBACK
            answer = "Our specialists are currently assisting other clients, but I can arrange a quick callback for you. What time today or tomorrow works best for you?"

        questions_answered = list(memory.questions_answered or [])
        questions_answered.append(
            {
                "question": question,
                "answer": answer,
                "sources": ["Human Escalation Policy"],
                "action": FollowUpAction.ESCALATE,
                "timestamp": utcnow().isoformat(),
            }
        )

        repo.update_call_memory(
            self._session,
            memory,
            {
                "handoff_status": (
                    HandoffStatus.INITIATED
                    if handoff_type == HandoffKind.WARM_TRANSFER
                    else HandoffStatus.REQUESTED
                ),
                "customer_intent": "request_human",
                "questions_answered": questions_answered,
                "last_customer_question": question,
                "last_agent_answer": answer,
            },
        )
        self._sync_call_metadata(call, memory)
        self._session.commit()

        return FollowUpQuestionResponse(
            answer=answer,
            source_references=["Human Escalation Policy"],
            should_handoff=True,
            handoff_recommended=True,
            handoff_reason="Customer requested live human assistance",
            handoff_type=handoff_type,
            handoff_id=handoff.id,
            action=FollowUpAction.ESCALATE,
            confidence=1.0,
            memory_state=self._memory_summary(memory),
        )

    def _handle_clarification(
        self,
        business: Business,
        call: Call,
        memory: VoiceCallMemory,
        question: str,
        clarification_msg: str,
    ) -> FollowUpQuestionResponse:
        questions_answered = list(memory.questions_answered or [])
        questions_answered.append(
            {
                "question": question,
                "answer": clarification_msg,
                "sources": [],
                "action": FollowUpAction.CLARIFY,
                "timestamp": utcnow().isoformat(),
            }
        )

        repo.update_call_memory(
            self._session,
            memory,
            {
                "customer_intent": "ambiguous_question",
                "questions_answered": questions_answered,
                "last_customer_question": question,
                "last_agent_answer": clarification_msg,
            },
        )
        self._sync_call_metadata(call, memory)

        repo.add_audit_log(
            self._session,
            action=AuditAction.FOLLOWUP_CLARIFY,
            business_id=business.id,
            actor="ai_voice_followup",
            entity_type="call",
            entity_id=call.id,
            decision="allow",
            reason_code="ambiguous_question",
            reason="Ambiguous question triggered clarification request",
            metadata={"question": question},
        )
        self._session.commit()

        return FollowUpQuestionResponse(
            answer=clarification_msg,
            source_references=[],
            should_handoff=False,
            handoff_recommended=False,
            handoff_reason=None,
            handoff_type=None,
            action=FollowUpAction.CLARIFY,
            confidence=0.5,
            memory_state=self._memory_summary(memory),
        )

    def _handle_unsupported_question(
        self,
        business: Business,
        call: Call,
        memory: VoiceCallMemory,
        question: str,
        sources: List[str],
    ) -> FollowUpQuestionResponse:
        """Never invent facts: acknowledge missing info and offer escalation."""
        if business.warm_transfer_enabled and (
            business.warm_transfer_number or business.handoff_target_number
        ):
            target = business.warm_transfer_number or business.handoff_target_number
            handoff = self._handoff_service.record_warm_transfer(
                call=call,
                reason=f"Question out of verified knowledge: {question}",
                summary="AI could not answer reliably from verified knowledge; transferring to specialist",
                transferred_to=target,
            )
            handoff_type = HandoffKind.WARM_TRANSFER
            answer = (
                "I don't have that specific information with me right now. Let me connect you directly "
                "with our specialist who can help answer that for you."
            )
        else:
            handoff = self._handoff_service.request_callback(
                business_id=business.id,
                lead_id=call.lead_id,
                call_id=call.id,
                reason=f"Question out of verified knowledge: {question}",
                briefing="Question unsupported by knowledge base; callback scheduled",
                requested_by="ai",
            )
            handoff_type = HandoffKind.CALLBACK
            answer = (
                "I don't have that specific information right in front of me, but I can have our team "
                "follow up with you directly. Would you like a quick callback with the details?"
            )

        questions_answered = list(memory.questions_answered or [])
        questions_answered.append(
            {
                "question": question,
                "answer": answer,
                "sources": sources,
                "action": FollowUpAction.ESCALATE,
                "timestamp": utcnow().isoformat(),
            }
        )

        repo.update_call_memory(
            self._session,
            memory,
            {
                "handoff_status": (
                    HandoffStatus.INITIATED
                    if handoff_type == HandoffKind.WARM_TRANSFER
                    else HandoffStatus.REQUESTED
                ),
                "questions_answered": questions_answered,
                "last_customer_question": question,
                "last_agent_answer": answer,
            },
        )
        self._sync_call_metadata(call, memory)

        repo.add_audit_log(
            self._session,
            action=AuditAction.FOLLOWUP_ESCALATED,
            business_id=business.id,
            actor="ai_voice_followup",
            entity_type="call",
            entity_id=call.id,
            decision="allow",
            reason_code="knowledge_missing",
            reason="Question could not be answered reliably from verified knowledge; escalated",
            metadata={"question": question, "handoff_type": handoff_type},
        )
        self._session.commit()

        return FollowUpQuestionResponse(
            answer=answer,
            source_references=sources,
            should_handoff=True,
            handoff_recommended=True,
            handoff_reason="The requested information is not in the verified business knowledge base",
            handoff_type=handoff_type,
            handoff_id=handoff.id,
            action=FollowUpAction.ESCALATE,
            confidence=0.0,
            memory_state=self._memory_summary(memory),
        )

    def _generate_grounded_answer(
        self, business: Business, question: str, retrieval
    ) -> Tuple[str, float]:
        """Generate answer grounded strictly in verified knowledge items."""
        facts_text = "\n\n".join(
            f"[{item.category.upper()}] {item.title}: {item.content} (Source: {item.source_reference})"
            for item in retrieval.items
        )

        top_item = retrieval.items[0]

        # Use LLM if available
        if self._llm and not isinstance(self._llm, NullLLMClient) and self._llm.is_available:
            system_prompt = (
                "You are Callora AI voice sales assistant answering a customer's question during a live phone call.\n"
                "STRICT AI GUARDRAILS:\n"
                "1. Answer using ONLY the verified facts provided below. NEVER invent prices, discounts, features, or terms.\n"
                "2. Keep the answer spoken-friendly, conversational, and concise: exactly 1 or 2 sentences.\n"
                "3. Do NOT use bullet points, bolding, markdown, asterisks, or URLs.\n"
                "4. Return a JSON object with keys: 'answer' (string) and 'confidence' (float between 0.0 and 1.0).\n\n"
                f"VERIFIED BUSINESS FACTS:\n{facts_text}"
            )
            user_prompt = f"Customer Question: {question}"

            try:
                res = self._llm.complete_json(system_prompt, user_prompt)
                raw_answer = str(res.get("answer") or "").strip()
                if raw_answer:
                    # Grounding verification guardrail
                    if AIGuardrails.verify_grounding(raw_answer, facts_text):
                        clean_answer = AIGuardrails.format_voice_response(raw_answer)
                        confidence = float(res.get("confidence") or retrieval.top_confidence)
                        return clean_answer, confidence
                    else:
                        logger.warning("ai_guardrail_ungrounded_answer_rejected", extra={"answer": raw_answer})
            except Exception as exc:
                logger.warning("llm_answer_generation_failed", extra={"error": str(exc)})

        # Deterministic grounded fallback
        clean_content = AIGuardrails.format_voice_response(top_item.content)
        return clean_content, retrieval.top_confidence

    def _resolve_next_qualification_question(
        self, business: Business, memory: VoiceCallMemory
    ) -> Optional[str]:
        """Determine next qualification question to continue sales flow naturally."""
        if not business.qualification_questions:
            return None

        answered_keys = set((memory.qualification_fields or {}).keys())

        for q in business.qualification_questions:
            if isinstance(q, dict):
                key = str(q.get("key") or "")
                text = str(q.get("question") or q.get("text") or "")
                if key and key not in answered_keys and text:
                    return f"To see if this is the best fit for your team, may I ask: {text}"
            elif isinstance(q, str) and q:
                # String question
                return f"To see if this is the best fit for you, may I ask: {q}"

        return None

    def _sync_call_metadata(self, call: Call, memory: VoiceCallMemory) -> None:
        metadata = dict(call.call_metadata or {})
        metadata["memory"] = self._memory_summary(memory)
        repo.update_call(self._session, call, {"call_metadata": metadata})

    def _memory_summary(self, memory: VoiceCallMemory) -> Dict[str, Any]:
        return {
            "call_id": memory.call_id,
            "business_id": memory.business_id,
            "turns_count": memory.turns_count,
            "questions_answered_count": len(memory.questions_answered or []),
            "qualification_fields": memory.qualification_fields or {},
            "pending_qualification_keys": memory.pending_qualification_keys or [],
            "handoff_status": memory.handoff_status,
            "opt_out_detected": memory.opt_out_detected,
            "customer_intent": memory.customer_intent,
        }
