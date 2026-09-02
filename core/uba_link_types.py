"""
Registry of the UBA link types.

A `UserBusinessAccess.link_type` is the *credential* a user acts under on a business
object ("accountant", "claim admin"), not an openIMIS `Role`. The same person may hold
several of them on the same object, one row each.

Keeping it a registered code rather than a foreign key means the frontend and the
permission checks name something stable: roles are deployment data and get renamed,
recreated or duplicated, a code does not. Modules declare theirs in `AppConfig.ready()`:

    from core.uba_link_types import register_uba_link_type

    register_uba_link_type(
        "HF_CLAIM_ADMIN",
        _("Claim administrator of the health facility"),
        models=("location.healthfacility",),
    )

The registry drives the model validation, the django admin picker and, later, the
frontend query. It never carries rights: those stay on `RoleRight`, so an implementer
keeps configuring them through the roles.
"""
import logging
from collections import namedtuple

logger = logging.getLogger(__name__)

UbaLinkType = namedtuple("UbaLinkType", ["code", "label", "models"])

_REGISTRY = {}

MAX_CODE_LENGTH = 64


def register_uba_link_type(code, label=None, models=()):
    """
    Declare a link type. `models` is the `<app_label>.<model>` labels the type may be
    used on, empty meaning any. Re-registering the same code replaces it, so a module
    reloaded by the test runner does not pile up duplicates.
    """
    code = str(code).strip()
    if not code:
        raise ValueError("A UBA link type needs a non empty code")
    if len(code) > MAX_CODE_LENGTH:
        raise ValueError(f"UBA link type code '{code}' exceeds {MAX_CODE_LENGTH} characters")
    models = tuple(str(model).lower() for model in models or ())
    if code in _REGISTRY and _REGISTRY[code] != UbaLinkType(code, label or code, models):
        logger.info("Redefining UBA link type '%s'", code)
    _REGISTRY[code] = UbaLinkType(code, label or code, models)
    return _REGISTRY[code]


def unregister_uba_link_type(code):
    """Drop a link type, mostly so tests can clean up after themselves."""
    _REGISTRY.pop(str(code).strip(), None)


def get_uba_link_type(code):
    return _REGISTRY.get(str(code).strip()) if code else None


def get_uba_link_types(model_label=None):
    """
    Registered link types, sorted by code. `model_label` restricts to the ones usable on
    that `<app_label>.<model>`, types declaring no model being usable everywhere.
    """
    types = sorted(_REGISTRY.values(), key=lambda link_type: link_type.code)
    if model_label is None:
        return types
    model_label = str(model_label).lower()
    return [t for t in types if not t.models or model_label in t.models]


def uba_link_type_choices(model_label=None):
    """`(code, label)` pairs for a form field or a django admin picker."""
    return [(t.code, t.label) for t in get_uba_link_types(model_label)]


def is_valid_for(code, model_label=None):
    """Is `code` registered, and usable on `model_label` when one is given ?"""
    link_type = get_uba_link_type(code)
    if link_type is None:
        return False
    if model_label is None or not link_type.models:
        return True
    return str(model_label).lower() in link_type.models


def normalize_link_types(link_types):
    """Accept a single code or an iterable of codes, return a list of clean codes."""
    if not link_types:
        return []
    if isinstance(link_types, str):
        link_types = [link_types]
    return [str(code).strip() for code in link_types if str(code).strip()]
