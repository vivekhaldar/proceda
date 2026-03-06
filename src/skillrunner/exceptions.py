class SkillRunnerError(Exception):
    """Base class for package errors."""


class SkillLoadError(SkillRunnerError):
    pass


class SkillParseError(SkillRunnerError):
    pass


class ValidationError(SkillRunnerError):
    pass


class RuntimeExecutionError(SkillRunnerError):
    pass


class UserCancelledError(SkillRunnerError):
    pass
