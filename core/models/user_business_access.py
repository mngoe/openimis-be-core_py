import logging

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from graphql import ResolveInfo

from ..uba_link_types import get_uba_link_type, is_valid_for, normalize_link_types
from .openimis_model import OpenIMISBusinessModel

logger = logging.getLogger(__name__)


def resolve_business_content_type(model_label):
    """
    Resolve the `<app_label>.<model>` part of a business map into a ContentType.
    Returns None (and logs) when the label does not match an installed model.
    """
    if isinstance(model_label, ContentType):
        return model_label
    try:
        app_label, model = str(model_label).lower().split(".", 1)
    except ValueError:
        logger.warning("Invalid business map model label '%s', expected '<app_label>.<model>'", model_label)
        return None
    try:
        return ContentType.objects.get_by_natural_key(app_label, model)
    except ContentType.DoesNotExist:
        logger.warning("Business map model label '%s' does not match any installed model", model_label)
        return None


def resolve_business_object_ids(content_type, object_ref):
    """
    UserBusinessAccess stores the primary key of the business object, but callers
    usually hold its uuid (`['location.healthfacility', hf_uuid]`). Return every
    stored object_id the reference may designate.
    """
    object_ids = {str(object_ref)}
    model = content_type.model_class() if content_type else None
    if model is not None and any(f.name == "uuid" for f in model._meta.fields):
        try:
            pk = model.objects.filter(uuid=object_ref).values_list("pk", flat=True).first()
            if pk is not None:
                object_ids.add(str(pk))
        except (ValidationError, ValueError, TypeError):
            # the reference is not a valid uuid for that model, the pk match is the only option
            pass
    return object_ids


def business_map_content_types(model_label, link_types):
    """
    The content types a business map is matched on: the one it names, or, when it names
    none, the models the credentials it demands are declared on - the registry
    (`core.uba_link_types`) being what knows them, so a caller only has to name the
    credential. `[None]` when neither says anything, the object reference and the
    credential being the whole demand then.
    """
    if model_label:
        content_type = resolve_business_content_type(model_label)
        return [content_type] if content_type is not None else []
    labels = []
    for code in link_types:
        link_type = get_uba_link_type(code)
        for model in (link_type.models if link_type else ()):
            if model not in labels:
                labels.append(model)
    if not labels:
        # a credential usable on any model is matched on the reference alone; a map
        # naming neither a model nor a credential demands nothing and matches nothing
        return [None] if link_types else []
    return [ct for ct in (resolve_business_content_type(label) for label in labels) if ct is not None]


def normalize_access_requirements(access_requirements):
    """
    Normalize the business map(s) passed to `has_perms`. Accepts a single map or a
    list of maps, a map being `[<model label>, <object reference>]` optionally followed
    by the link type(s) it demands: `['location.healthfacility', hf_uuid, 'ACCOUNTANT']`
    or `['location.healthfacility', hf_uuid, ['ACCOUNTANT', 'HF_CLAIM_ADMIN']]`.

    The model label may be None when the map demands a credential: the registry already
    says which models that credential is declared on, so `[None, hf_uuid, 'CLAIM_ADMIN']`
    asks the same question without the caller repeating the model.

    Returns a list of `(model_label, object_reference, link_types)`, `link_types` being
    an empty list when the map accepts any credential on the instance.
    """
    if not access_requirements:
        return []
    if not isinstance(access_requirements, (list, tuple)):
        logger.warning("Ignoring invalid access requirements %s", access_requirements)
        return []
    # a single map starts with its model label, a list of maps with a map
    if access_requirements[0] is None or isinstance(access_requirements[0], str):
        access_requirements = [access_requirements]
    business_maps = []
    for business_map in access_requirements:
        if not business_map or len(business_map) < 2:
            logger.warning("Ignoring invalid business map %s", business_map)
            continue
        link_types = normalize_link_types(business_map[2]) if len(business_map) > 2 else []
        business_maps.append((business_map[0], business_map[1], link_types))
    return business_maps


class UserBusinessAccess(OpenIMISBusinessModel):
    """
    Generic "this user is linked to that business object" association: the single
    table generalizing the dedicated ones (claim admin -> health facility,
    OfficerVillage, PolicyHolderUser, ...).

    It does not carry any right: rights stay on RoleRight, the ones flagged
    `uba=True` being granted only on the instances the user has a link on.
    """
    user = models.ForeignKey('core.User', models.CASCADE, related_name="business_accesses")
    link_type = models.CharField(
        max_length=64, db_index=True,
        help_text="The credential the user acts under on that object, a code registered "
                  "through core.uba_link_types (not an openIMIS role). The same user may "
                  "hold several credentials on one object, one row each.")
    content_type = models.ForeignKey(ContentType, models.CASCADE, null=True, blank=True)
    object_id = models.CharField(max_length=36, null=True, blank=True)
    business_object = GenericForeignKey('content_type', 'object_id')

    def __str__(self):
        return "%s -> %s[%s] (%s)" % (self.user, self.content_type, self.object_id, self.link_type)

    @property
    def model_label(self):
        return f"{self.content_type.app_label}.{self.content_type.model}" if self.content_type else None

    def clean(self):
        """Reject a link type that is not registered, or not declared for that model."""
        super().clean()
        if not is_valid_for(self.link_type, self.model_label):
            raise ValidationError({
                "link_type": f"'{self.link_type}' is not a UBA link type registered for "
                             f"{self.model_label or 'any model'}"
            })

    @classmethod
    def filter_for_user(cls, user, content_type=None, object_ids=None, link_types=None, now=None):
        """
        Links of `user` that are active and within their validity window, optionally
        restricted to a content type, a set of object ids and a set of link types.

        Unlike the first draft this no longer joins on the roles: the link only says
        which credential the user holds on the instance. Whether a right actually
        follows is answered by the rights bag, which is built from the roles the user
        holds *now*, so revoking a role still strips the right immediately.
        """
        from .user import InteractiveUser, User

        i_user = InteractiveUser.is_interactive_user(user)
        if i_user is None:
            # rights are carried by the interactive user, a link cannot grant anything without one
            return cls.objects.none()
        core_user = user if isinstance(user, User) else i_user.user
        if core_user is None:
            return cls.objects.none()
        if now is None:
            from core import datetime
            now = datetime.datetime.now()
        queryset = cls.objects.filter(
            user_id=core_user.id,
            active=True,
            date_valid_from__lte=now,
        ).filter(Q(date_valid_to__isnull=True) | Q(date_valid_to__gte=now))
        if content_type is not None:
            queryset = queryset.filter(content_type=content_type)
        if object_ids is not None:
            queryset = queryset.filter(object_id__in=object_ids)
        if link_types:
            queryset = queryset.filter(link_type__in=link_types)
        return queryset

    @classmethod
    def has_access(cls, user, business_maps, now=None):
        """
        Does `user` hold a valid link on one of the business objects of `business_maps`,
        under one of the link types the map demands ? A map naming no link type is
        satisfied by any credential on that instance, and a map naming no model is
        matched on the models its credentials are registered on.
        """
        for model_label, object_ref, link_types in business_maps:
            unknown = [code for code in link_types if not is_valid_for(code, model_label)]
            if unknown:
                logger.warning("Business map %s demands unregistered link type(s) %s", model_label, unknown)
            for content_type in business_map_content_types(model_label, link_types):
                object_ids = resolve_business_object_ids(content_type, object_ref)
                if cls.filter_for_user(user, content_type, object_ids, link_types, now=now).exists():
                    return True
        return False

    @classmethod
    def get_queryset(cls, queryset, user):
        if isinstance(user, ResolveInfo):
            user = user.context.user
        if settings.ROW_SECURITY and user.is_anonymous:
            return queryset.none()
        return queryset

    class Meta:
        verbose_name = "User Business Access"
        verbose_name_plural = "User Business Accesses"
        # business actions beyond django's default add/change/delete/view, so
        # core.apps.DJANGO_PERMS can name them and an admin can grant them
        permissions = [
            ("activate_userbusinessaccess", "Can activate a user business access"),
            ("deactivate_userbusinessaccess", "Can deactivate a user business access"),
        ]
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
            models.Index(fields=['user', 'content_type', 'object_id']),
            models.Index(fields=['user', 'link_type']),
            models.Index(fields=['link_type', 'object_id']),
        ]
