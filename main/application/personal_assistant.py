from __future__ import annotations

class PersonalAssistantService:
    """Personal track history facade. Execution belongs exclusively to RunCoordinator."""

    track_id = "assistant:personal"

    def __init__(self, runtime, conversation):
        self.conversation = conversation

    def history(self, limit: int = 50, before_sequence: int | None = None):
        return self.conversation.repository.track_history(mode="assistant", scope_id="personal", track_id=self.track_id, limit=limit, before_sequence=before_sequence)
