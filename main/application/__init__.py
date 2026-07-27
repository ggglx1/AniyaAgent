"""Product application composition root and mode-specific use cases.

Imports stay lazy because low-level runtime modules also use application-owned
infrastructure such as the durable run-event store.
"""

__all__ = ["AniyaApplication", "create_application"]


def __getattr__(name: str):
    if name in __all__:
        from .bootstrap import AniyaApplication, create_application
        return {"AniyaApplication": AniyaApplication, "create_application": create_application}[name]
    raise AttributeError(name)
