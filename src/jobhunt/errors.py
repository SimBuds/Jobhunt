"""Exception hierarchy for jobhunt. CLI catches these and exits cleanly.

Each class declares the process exit code `cli._run` uses for it. The table is
a contract with the automated caller, shared with SEO-LLM (2026-11-03):
0 ok, 1 unhandled exception (a bug), 2 usage, 10 config or input missing or
invalid, 11 needs a person, 12 a stage failed, 13 router unavailable (retry
later), 14 reserved for "busy, retry later" and not raised here. A class
attribute rather than a lookup in `_run`, so a subclass can never be shadowed
by a check for its parent that happens to run first.
"""


class JobHuntError(Exception):
    """Base for all jobhunt domain errors."""

    exit_code = 12


class ConfigError(JobHuntError):
    exit_code = 10


class InputError(JobHuntError):
    """A missing or invalid input the user can fix: the profile, a job
    reference, the working directory."""

    exit_code = 10


class MigrationError(JobHuntError):
    exit_code = 10


class IngestError(JobHuntError):
    pass


class GatewayError(JobHuntError):
    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        # Diagnostic text that may hold model output (and so resume text). The
        # CLI prints it only under --debug, never on the default error line.
        self.detail = detail


class GatewayUnavailableError(GatewayError):
    """The router is unreachable or the model could not load. Retry later."""

    exit_code = 13


class PipelineError(JobHuntError):
    pass


class BrowserError(JobHuntError):
    pass


class PrivacyError(JobHuntError):
    """Personal data found inside the repository checkout."""

    exit_code = 10


class NeedsPersonError(JobHuntError):
    """The command needs a decision or input only a person can give."""

    exit_code = 11
