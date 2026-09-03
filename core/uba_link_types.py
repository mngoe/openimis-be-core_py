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
        params={"location_field": "location"},
    )

The registry drives the model validation, the django admin picker, the frontend query and
the row level filtering. It never carries rights: those stay on `RoleRight`, so an
implementer keeps configuring them through the roles.

`params` is the open bag a module uses to say how the credential relates to the location
tree, so that the row filter can work it out instead of every call site repeating it.
The keys `location` understands are documented on `LOCATION_PARAMS` below; a module may
put anything else in there, unknown keys being ignored.
"""
import logging
from collections import namedtuple

logger = logging.getLogger(__name__)

UbaLinkType = namedtuple("UbaLinkType", ["code", "label", "models", "params"])

# The `params` keys the location row filter reads. A credential is tied to the location
# tree either because it is held on a Location (`location_type`) or because it is held on
# a model hanging off one (`location_field`), and the filter needs to know which to turn
# a location path into a path to the linked object.
LOCATION_PARAMS = {
    "location_type": "The loc_type the linked Location sits at, 'V' for a village "
                     "credential. Declared as a code rather than a depth because "
                     "`LocationConfig.location_types` is deployment configuration.",
    "location_field": "For a credential held on a model that hangs off a location (a "
                      "health facility), the field of that model pointing at the "
                      "Location, so the filter can hop from a location path to it.",
    "include_descendants": "A link also covers what sits below the linked location, so "
                           "a link granted on a district reaches its villages.",
    "include_ancestors": "A link also covers what sits above the linked location.",
}

_REGISTRY = {}

MAX_CODE_LENGTH = 64


def register_uba_link_type(code, label=None, models=(), params=None):
    """
    Declare a link type. `models` is the `<app_label>.<model>` labels the type may be
    used on, empty meaning any. `params` is the open bag described in the module
    docstring, `LOCATION_PARAMS` listing the keys the row filter reads. Re-registering
    the same code replaces it, so a module reloaded by the test runner does not pile up
    duplicates.
    """
    code = str(code).strip()
    if not code:
        raise ValueError("A UBA link type needs a non empty code")
    if len(code) > MAX_CODE_LENGTH:
        raise ValueError(f"UBA link type code '{code}' exceeds {MAX_CODE_LENGTH} characters")
    models = tuple(str(model).lower() for model in models or ())
    params = dict(params or {})
    entry = UbaLinkType(code, label or code, models, params)
    if code in _REGISTRY and _REGISTRY[code] != entry:
        logger.info("Redefining UBA link type '%s'", code)
    _REGISTRY[code] = entry
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


def get_uba_link_type_param(code, name, default=None):
    """One `params` entry of a registered link type, `default` when either is missing."""
    link_type = get_uba_link_type(code)
    return link_type.params.get(name, default) if link_type else default


def get_location_aware_uba_link_types():
    """
    The link types that say how they relate to the location tree, i.e. the ones the
    location row filter can turn into a queryset filter. Sorted by code, like
    `get_uba_link_types`, so a filter is built in a stable order.
    """
    return [
        link_type for link_type in get_uba_link_types()
        if link_type.params.get("location_type") or link_type.params.get("location_field")
    ]
