from __future__ import annotations

"""Compatibility module that also exposes the production application package."""

from pathlib import Path

# ``application.py`` existed before the package was introduced. Giving this module a
# package path preserves old imports while allowing the new composition to be modular.
__path__ = [str(Path(__file__).with_suffix(""))]

from .application.bootstrap import AniyaApplication, create_application  # noqa: E402

PersonalAssistantApplication = AniyaApplication

__all__ = ["AniyaApplication", "PersonalAssistantApplication", "create_application"]
