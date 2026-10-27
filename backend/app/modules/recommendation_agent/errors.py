"""Recommendation Agent errors."""

from app.errors import AppError


class RecommendationNotFoundError(AppError):
    status_code = 404
    code = "recommendation_not_found"

    def __init__(self, recommendation_id: str):
        super().__init__(f"Recommendation '{recommendation_id}' was not found")
        self.recommendation_id = recommendation_id


class ScriptSuggestionNotFoundError(AppError):
    status_code = 404
    code = "script_suggestion_not_found"

    def __init__(self, suggestion_id: str):
        super().__init__(f"Script suggestion '{suggestion_id}' was not found")
        self.suggestion_id = suggestion_id


class InsufficientDataError(AppError):
    status_code = 400
    code = "insufficient_data"

    def __init__(self, message: str):
        super().__init__(message)


class EvidenceValidationError(AppError):
    status_code = 400
    code = "evidence_validation_failed"

    def __init__(self, message: str):
        super().__init__(message)
