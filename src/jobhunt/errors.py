"""Exception hierarchy for jobhunt. CLI catches these and exits cleanly."""


class JobHuntError(Exception):
    """Base for all jobhunt domain errors."""


class ConfigError(JobHuntError):
    pass


class MigrationError(JobHuntError):
    pass


class IngestError(JobHuntError):
    pass


class GatewayError(JobHuntError):
    pass


class PipelineError(JobHuntError):
    pass


class BrowserError(JobHuntError):
    pass
