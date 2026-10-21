"""Prompts for the post-call workflow.

The prompts are written for a multilingual sales conversation: the agent may
speak Hindi, Kannada or English (and code-switch), so the instructions ask
for answers in the language the conversation actually used and never assume
English.

Every prompt asks for a single JSON object. The graph validates the answer
against a Pydantic model, so a malformed response is a parse error rather
than silently wrong data.
"""

from typing import Any, Dict, List, Optional

QUALIFICATION_SYSTEM = """You are a precise call-analysis engine for a sales team in India.
You read a phone-call transcript and extract structured facts.
You always answer with a single JSON object and nothing else.
Never invent an answer that is not supported by the transcript.
If the transcript does not contain the answer, use null or an empty string.
"""

QUALIFICATION_USER = """Below is the transcript of an outbound sales call.

<business>{business_name}</business>
<call_language>{language}</call_language>
<business_instructions>{business_instructions}</business_instructions>

<qualification_questions>
{questions}
</qualification_questions>

<transcript>
{transcript}
</transcript>

Answer these questions using only what the transcript supports.

Return exactly this JSON object:
{{
  "answers": {{"<question key>": "<answer or empty string>"}},
  "qualified": true or false,
  "score": 0.0 to 1.0,
  "notes": "<one short sentence explaining the score>",
  "missing_questions": ["<keys of questions that were not answered>"]
}}

Rules:
- "qualified" is true only when the lead clearly meets the business criteria.
- "score" is the confidence that the lead is qualified, between 0 and 1.
- Use the exact question keys given above.
"""

REFUSAL_SYSTEM = """You are a precise call-analysis engine for a sales team in India.
You read a phone-call transcript and classify why a conversation ended.
You always answer with a single JSON object and nothing else.
Never invent a reason that is not supported by the transcript.
"""

REFUSAL_USER = """Below is the transcript of an outbound sales call.

<call_language>{language}</call_language>

<transcript>
{transcript}
</transcript>

Return exactly this JSON object:
{{
  "is_refusal": true or false,
  "category": "<one of: not_interested, already_customer, wrong_number, too_early, price, competitor, no_budget, callback_later, do_not_contact, language_barrier, other>",
  "reason": "<what the lead said, in one short sentence>",
  "callback_requested": true or false
}}

Rules:
- "is_refusal" is true only when the lead declined or blocked the call.
- "category" must be exactly one of the listed values.
- Write the reason in the language the conversation used, but keep it to one sentence.
"""

SUMMARY_SYSTEM = """You are a sales assistant who writes short, factual call notes.
You always answer with a single JSON object and nothing else.
Never state anything that the transcript does not support.
"""

SUMMARY_USER = """Below is the transcript of an outbound sales call.

<business>{business_name}</business>
<call_language>{language}</call_language>
<business_instructions>{business_instructions}</business_instructions>

<transcript>
{transcript}
</transcript>

Return exactly this JSON object:
{{
  "summary": "<3-5 sentence summary of what happened>",
  "outcome": "<one of: qualified, not_qualified, refused, callback_requested, unknown>",
  "follow_ups": ["<concrete next action a salesperson should take>"],
  "language": "<the language the conversation actually used: en, hi, kn or a mix>",
  "key_details": {{"<short fact>": "<value>"}},
  "needs_human": true or false
}}

Rules:
- "summary" describes the conversation, not the sales pitch.
- "follow_ups" are concrete actions, empty list when nothing is needed.
- "needs_human" is true when a salesperson should take over the conversation.
"""

HANDOFF_SYSTEM = """You are a sales assistant who briefs a human sales representative.
You always answer with a single JSON object and nothing else.
"""

HANDOFF_USER = """You are briefing a human sales representative about a call.

<business>{business_name}</business>
<lead>{lead_name} ({lead_phone})</lead>
<call_language>{language}</call_language>

<summary>{summary}</summary>
<qualification>{qualification}</qualification>
<refusal>{refusal}</refusal>

Return exactly this JSON object:
{{
  "briefing": "<a concise briefing, at most 120 words, a salesperson can read in 20 seconds>",
  "recommended_action": "<the single next action>",
  "priority": "<one of: low, medium, high>"
}}

Rules:
- Lead with what the customer wants and what was decided.
- Mention any commitment (price, timeline, callback window) explicitly.
- Do not invent facts.
"""


def format_questions(questions: List[Dict[str, Any]]) -> str:
    """Render the business qualification questions for the prompt."""
    lines: List[str] = []
    for index, item in enumerate(questions or [], start=1):
        if isinstance(item, dict):
            key = item.get("key") or item.get("id") or f"q{index}"
            text = item.get("question") or item.get("text") or ""
            lines.append(f"- {key}: {text}")
        elif isinstance(item, str):
            lines.append(f"- q{index}: {item}")
    return "\n".join(lines) if lines else "- (no qualification questions configured)"


def format_answers(answers: Optional[Dict[str, Any]]) -> str:
    if not answers:
        return "(none)"
    return "\n".join(f"- {key}: {value}" for key, value in answers.items())


def truncate_transcript(transcript: str, max_chars: int) -> str:
    """Keep the transcript inside the model's budget.

    The beginning of a call contains the greeting and the identity of the
    agent; the end contains the outcome. When the transcript is too long
    both ends are kept and the middle is elided.
    """
    if not transcript:
        return ""
    if len(transcript) <= max_chars:
        return transcript
    head = max_chars // 3
    tail = max_chars - head
    return (
        transcript[:head]
        + "\n\n[... transcript truncated ...]\n\n"
        + transcript[-tail:]
    )
