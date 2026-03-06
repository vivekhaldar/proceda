from dataclasses import dataclass, field


@dataclass(slots=True)
class RuntimeContext:
    message_history: list[str] = field(default_factory=list)

    def add_message(self, message: str) -> None:
        self.message_history.append(message)
