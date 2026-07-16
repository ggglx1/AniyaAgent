class LLMError(RuntimeError):
    pass


class ApiAuthError(LLMError):
    pass


class ApiConfigError(LLMError):
    pass


class ApiConnectionError(LLMError):
    pass


class ApiTimeoutError(LLMError):
    pass


class ApiHTTPError(LLMError):
    pass
