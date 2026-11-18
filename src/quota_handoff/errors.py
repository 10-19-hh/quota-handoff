"""Error model and CLI exit codes (PRD section 8)."""

EXIT_OK = 0
EXIT_INPUT_ERROR = 2  # input / schema error
EXIT_UNCERTAIN = 3  # data uncertain or no recommendation possible
EXIT_SAFETY_GATE = 4  # safety gate refused the action (e.g. overwrite)


class QuotaHandoffError(Exception):
    exit_code = 1


class InputError(QuotaHandoffError):
    """Invalid or unsupported input. Carries a list of path-qualified issues."""

    exit_code = EXIT_INPUT_ERROR

    def __init__(self, message, issues=None):
        super().__init__(message)
        self.issues = list(issues or [])

    def __str__(self):
        base = super().__str__()
        if not self.issues:
            return base
        return base + "\n" + "\n".join("  - " + issue for issue in self.issues)


class SafetyGateError(QuotaHandoffError):
    exit_code = EXIT_SAFETY_GATE
