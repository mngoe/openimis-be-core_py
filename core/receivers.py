"""
Invalidation of the per interactive user rights caches when the RBAC tables change.

The `rights_<id>` and `is_admin_<id>` keys are written by `InteractiveUser.rights_str`
and `InteractiveUser.is_imis_admin`; nothing expires them before their TTL, so a role
or an assignment changed under a running instance stayed invisible for up to 10 minutes.
"""
import logging
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.models.user import Role, RoleRight, UserRole

logger = logging.getLogger(__name__)

RIGHTS_CACHE_PREFIX = "rights_"
IS_ADMIN_CACHE_PREFIX = "is_admin_"


def clear_user_rights_cache(user_id):
    """Drop the cached rights of a single interactive user."""
    cache.delete_many([
        f"{RIGHTS_CACHE_PREFIX}{user_id}",
        f"{IS_ADMIN_CACHE_PREFIX}{user_id}",
    ])


def clear_role_rights_cache(role_id):
    """
    Drop the cached rights invalidated by a change on `role_id`.

    Those keys are per user, so a role level change has no single key to target, hence
    the two modes:

    - backend exposing `delete_pattern` (django-redis): one wildcard call per prefix.
    - backend without it (memcached, locmem): the users holding the role are resolved
      from UserRole and their keys dropped one by one. `cache.clear()` is deliberately
      not used, it would also flush the object cache (`cs_*`) held by CachedModelMixin.
    """
    delete_pattern = getattr(cache, "delete_pattern", None)
    if delete_pattern is not None:
        delete_pattern(f"{RIGHTS_CACHE_PREFIX}*")
        delete_pattern(f"{IS_ADMIN_CACHE_PREFIX}*")
        return

    user_ids = UserRole.objects.filter(
        role_id=role_id, validity_to__isnull=True
    ).values_list("user_id", flat=True).distinct()
    keys = []
    for user_id in user_ids:
        keys.append(f"{RIGHTS_CACHE_PREFIX}{user_id}")
        keys.append(f"{IS_ADMIN_CACHE_PREFIX}{user_id}")
    if keys:
        cache.delete_many(keys)


@receiver([post_save, post_delete], sender=Role, dispatch_uid="core_role_rights_cache")
def _role_receiver(sender, instance, **kwargs):
    # is_system drives is_imis_admin, is_blocked and the validity drive the whole bag
    clear_role_rights_cache(instance.pk)


@receiver([post_save, post_delete], sender=RoleRight, dispatch_uid="core_roleright_rights_cache")
def _role_right_receiver(sender, instance, **kwargs):
    # the global bag depends on RoleRight.uba, both bags have to be recomputed
    clear_role_rights_cache(instance.role_id)


@receiver([post_save, post_delete], sender=UserRole, dispatch_uid="core_userrole_rights_cache")
def _user_role_receiver(sender, instance, **kwargs):
    # a single user is concerned, no wildcard needed on either backend
    clear_user_rights_cache(instance.user_id)
