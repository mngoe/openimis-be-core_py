"""
Row level filtering driven by the UBA links.

`UserBusinessAccess` says which business objects a user acts on. Turning that into a
queryset filter is the same operation in every module - "keep the rows whose health
facility / village / ... is one the user is linked to" - so it lives here rather than
being re-derived per module.

The single knob a caller needs is `prefix`, the field path from the model being filtered
to the linked object, because the linked object is rarely the model itself:

    Claim  -> health facility : prefix='health_facility'
    Family -> village         : prefix='location'
    Insuree-> village         : prefix='family__location'
    HealthFacility            : prefix='' (the model *is* the linked object)

`build_uba_filter_query` returns None when the user holds no link of that type, which is
what tells a caller to fall back to its usual location based row security instead of
narrowing to an empty set.
"""
import logging

from django.db.models import Q

logger = logging.getLogger(__name__)


def _content_types(link_types, model_label):
    from .models.user_business_access import business_map_content_types

    return business_map_content_types(model_label, link_types)


def business_access_object_ids(user, link_types=None, model_label=None, now=None):
    """
    Primary keys of the business objects `user` holds a valid link on.

    `link_types` is a code or a list of codes, `model_label` an `<app_label>.<model>`;
    leaving the label out lets the registry (`core.uba_link_types`) say which models the
    demanded credentials are declared on, so a caller only names the credential.

    Only active links inside their validity window count, `UserBusinessAccess` owning
    that definition. The ids are returned as ints, `object_id` being a CharField.
    """
    from .models.user_business_access import UserBusinessAccess
    from .uba_link_types import normalize_link_types

    link_types = normalize_link_types(link_types)
    object_ids = set()
    for content_type in _content_types(link_types, model_label):
        stored = UserBusinessAccess.filter_for_user(
            user, content_type=content_type, link_types=link_types, now=now
        ).values_list("object_id", flat=True)
        object_ids.update(int(value) for value in stored if str(value).isdigit())
    return sorted(object_ids)


def build_uba_filter_query(user, link_types=None, model_label=None, prefix='',
                           queryset=None, include_null=False, now=None):
    """
    Narrow rows down to the business objects `user` holds a valid UBA link on.

    `prefix` is the field path from the filtered model to the linked object, '' when the
    model is the linked object itself. It also accepts several paths, OR'ed together, for
    the models reachable by more than one route - an insuree is located either by its own
    village or by its family's. `include_null` additionally keeps the rows whose path is
    null, off by default: on a narrowing filter a missing link target is not a match.

    Returns the `Q` (or the filtered `queryset` when one is given) when the user holds at
    least one such link, and None when they hold none - the caller then keeps whatever
    row security it applies otherwise, rather than filtering everything out.

    The links are read once whatever the number of paths, so adding a path costs a join,
    not a query.
    """
    object_ids = business_access_object_ids(user, link_types, model_label, now=now)
    if not object_ids:
        return None
    uba_filter = uba_filter_from_object_ids(object_ids, prefix=prefix, include_null=include_null)
    if queryset is not None:
        return queryset.filter(uba_filter)
    return uba_filter


def uba_filter_from_object_ids(object_ids, prefix='', include_null=False):
    """
    The `Q` narrowing `prefix` down to `object_ids`.

    Split out of `build_uba_filter_query` because a caller that already resolved the ids
    itself - `location` widens them up and down the tree before filtering - should not
    have to restate how a path is turned into a lookup.
    """
    prefixes = [prefix] if isinstance(prefix, str) else list(prefix) or ['']
    uba_filter = Q()
    for path in prefixes:
        path_filter = Q((f"{path}__id__in" if path else "id__in", object_ids))
        if include_null:
            path_filter |= Q((f"{path}__isnull" if path else "id__isnull", True))
        uba_filter |= path_filter
    return uba_filter


def has_business_access_links(user, link_types=None, model_label=None, now=None):
    """Does `user` hold any valid link of that type ? Cheaper than building the id list."""
    from .models.user_business_access import UserBusinessAccess
    from .uba_link_types import normalize_link_types

    link_types = normalize_link_types(link_types)
    for content_type in _content_types(link_types, model_label):
        if UserBusinessAccess.filter_for_user(
            user, content_type=content_type, link_types=link_types, now=now
        ).exists():
            return True
    return False
