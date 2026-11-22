"""Safe errors crossing the JSON API boundary."""


class ObservatoryError(Exception):
    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


class AuthenticationRequired(ObservatoryError):
    def __init__(self) -> None:
        super().__init__("authentication_required", "Sign in to Telegram first.")


class InvalidCredentials(ObservatoryError):
    def __init__(self) -> None:
        super().__init__("invalid_api_credentials", "Check the Telegram API ID and API hash.")


class InvalidRequest(ObservatoryError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_request", message)


class EmptyMessageRange(ObservatoryError):
    def __init__(self) -> None:
        super().__init__("empty_message_range", "No indexed messages exist in the selected range.")


class ConversationUnavailable(InvalidRequest):
    def __init__(self) -> None:
        super().__init__("This conversation is no longer in the account's accessible dialogs. Sync dialogs and select an accessible conversation.")
        self.code = "conversation_unavailable"


class MemberGroupsUnavailable(InvalidRequest):
    def __init__(self) -> None:
        super().__init__("Could not retrieve participants from any selected group.")
        self.code = "members_unavailable"
