"""OP-3317 phase 2: the GraphQL arguments a connection already takes, mapped
onto the OP-2029 ``validity`` / ``audits`` contract of ``Model.get_queryset``.

    showHistory: true          -> audits=ALL
    isDeleted: true            -> validity=DELETED_NOW
    isDeleted: null (explicit) -> validity=ALL
    dateValidFrom__Gte / dateValidTo__Lte
                               -> validity=between(from, to)  (business-validity models only)

A caller passing none of them gets the model's own default, so nothing changes
for the queries that do not ask (phase 1 rule).

The connection field stores the mapping on the request for the time graphene
calls ``DjangoObjectType.get_queryset(queryset, info)`` -- which receives no
arguments (graphene_django/fields.py) -- and ``ScopedQuerysetMixin`` hands it
to the model.
"""
import inspect
from contextlib import contextmanager

from core.models.row_filters import Audits, Validity

_CONTEXT_ATTR = "_openimis_row_filters"


def _arg(args, *names):
    for name in names:
        if name in args:
            return True, args[name]
    return False, None


def row_filters_from_args(args):
    """{'validity': ..., 'audits': ..., 'validity_range': (from, to)} from connection args."""
    rows = {}
    present, show_history = _arg(args, "showHistory", "show_history")
    if present and show_history:
        rows["audits"] = Audits.ALL
    present, is_deleted = _arg(args, "isDeleted", "is_deleted")
    if present:
        rows["validity"] = Validity.DELETED_NOW if is_deleted else (
            Validity.ALL if is_deleted is None else Validity.NOW)
    _, valid_from = _arg(args, "dateValidFrom__Gte", "date_valid_from__gte")
    _, valid_to = _arg(args, "dateValidTo__Lte", "date_valid_to__lte")
    if valid_from is not None or valid_to is not None:
        rows["validity_range"] = (valid_from, valid_to)
    return rows


@contextmanager
def row_filters_in_context(info, args):
    context = getattr(info, "context", None)
    if context is None:
        yield
        return
    previous = getattr(context, _CONTEXT_ATTR, None)
    setattr(context, _CONTEXT_ATTR, row_filters_from_args(args))
    try:
        yield
    finally:
        setattr(context, _CONTEXT_ATTR, previous)


def row_filter_kwargs(model, info):
    """The validity/audits kwargs to pass to ``model.get_queryset`` for this request."""
    rows = getattr(getattr(info, "context", None), _CONTEXT_ATTR, None) or {}
    kwargs = {k: rows[k] for k in ("validity", "audits") if k in rows}
    valid_range = rows.get("validity_range")
    if valid_range and "validity" not in kwargs:
        get_fields = getattr(model, "get_business_validity_fields", None)
        if callable(get_fields) and get_fields():
            kwargs["validity"] = Validity.between(*valid_range)
    return kwargs


_ACCEPTS = {}


def accepts_row_filters(get_queryset):
    """True when a get_queryset takes validity/audits (OP-2029) or **kwargs."""
    key = getattr(get_queryset, "__func__", get_queryset)
    if key not in _ACCEPTS:
        try:
            params = inspect.signature(get_queryset).parameters.values()
        except (TypeError, ValueError):
            _ACCEPTS[key] = False
        else:
            _ACCEPTS[key] = any(
                p.kind == p.VAR_KEYWORD or p.name in ("validity", "audits") for p in params)
    return _ACCEPTS[key]
