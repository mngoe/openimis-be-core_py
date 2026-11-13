"""
Single entry point for openIMIS cache access (ported from openimis-be-core_py
release/26.10, without its rights/user-flag key space).

Two reasons for everything to go through here:

* a cache is an optimisation. A backend that is down (or simply not deployed
  for the one-off containers that run ``migrate`` / ``load_fixtures``) must
  degrade to a cache miss, never abort the database operation that triggered
  the read or the invalidation. The ``cache_*`` helpers below own that
  tolerance so no call site has to remember it.
* the key spaces are named once, here, instead of being spelled out with an
  f-string at each call site.
"""

import logging

from django.conf import settings
from django.core.cache import cache, caches

logger = logging.getLogger(__name__)

_UNSET = object()


class UnknownCacheAliasError(ValueError):
    """Raised when a requested cache alias is not in settings.CACHES."""


def get_configured_cache_aliases():
    """Return cache aliases from settings, preserving configuration order."""
    aliases = list(getattr(settings, "CACHES", {}) or {})
    return aliases or ["default"]


def get_cache_info(alias):
    """Return display metadata for a configured cache alias."""
    conf = (getattr(settings, "CACHES", {}) or {}).get(alias, {})
    backend = conf.get("BACKEND") or caches[alias].__class__.__name__
    return {
        "alias": alias,
        "backend": backend,
        "backend_short": backend.rsplit(".", 1)[-1],
        "key_prefix": conf.get("KEY_PREFIX") or "",
        "location": conf.get("LOCATION") or "",
    }


def list_cache_info(aliases=None):
    """Return metadata for the given aliases, or all configured caches."""
    if aliases is None:
        aliases = get_configured_cache_aliases()
    return [get_cache_info(alias) for alias in aliases]


def resolve_cache_aliases(aliases=None):
    """
    Normalize the aliases to flush.

    ``None`` or an empty sequence means every configured cache. Unknown names
    raise ``UnknownCacheAliasError`` before any cache is touched.
    """
    available = get_configured_cache_aliases()
    if not aliases:
        return list(available)

    unknown = [alias for alias in aliases if alias not in available]
    if unknown:
        raise UnknownCacheAliasError(
            "Unknown cache alias(es): {}. Configured: {}".format(
                ", ".join(unknown),
                ", ".join(available),
            )
        )

    resolved = []
    seen = set()
    for alias in aliases:
        if alias not in seen:
            seen.add(alias)
            resolved.append(alias)
    return resolved


def flush_caches(aliases=None):
    """
    Clear the given Django cache aliases, or all configured caches.

    Returns ``(flushed, errors)`` where ``flushed`` is a list of aliases that
    were cleared and ``errors`` is a list of ``(alias, message)`` tuples.
    """
    resolved = resolve_cache_aliases(aliases)
    flushed = []
    errors = []
    for alias in resolved:
        try:
            caches[alias].clear()
            flushed.append(alias)
        except Exception as exc:
            errors.append((alias, str(exc)))
    return flushed, errors


# ---------------------------------------------------------------------------
# Expiry and coherence
#
# Every entry expires: the overall CACHE_OBJECT_TTL applies whenever a caller
# gives no timeout, or None. An entry that lives forever outlives any
# invalidation it misses.
#
# A process-local backend (LocMemCache) is only coherent when every write
# happens in the process that reads: with async mutations the write, and the
# invalidation its signals trigger, run in the Celery worker, and never reach
# the web processes' copies - nor the other way round. Such a cache is then not
# used at all: values live in a memo for the current request or task only.
# ---------------------------------------------------------------------------

PROCESS_LOCAL_BACKENDS = ("LocMemCache", "DummyCache")


def default_ttl():
    return getattr(settings, "CACHE_OBJECT_TTL", 3600)


def _timeout(timeout):
    return default_ttl() if timeout is _UNSET or timeout is None else timeout


def _runs_out_of_process():
    import core

    return bool(getattr(core, "async_mutations", False))


def cache_is_coherent(alias="default"):
    """Whether entries of this cache alias can be trusted across requests."""
    if get_cache_info(alias)["backend_short"] not in PROCESS_LOCAL_BACKENDS:
        return True
    return not _runs_out_of_process()


def _memo():
    # request (or Celery task) scoped; cleared by core.utils.clear_access_cache
    from core.utils import get_cache_memo

    return get_cache_memo()


def cache_memo_forget(prefixes, alias="default"):
    """Drop the request memo's entries whose key starts with one of ``prefixes``.

    For invalidations that cannot name their keys (a whole key space), next to
    whatever the caller does on the backend itself.
    """
    prefixes = tuple(prefixes)
    memo = _memo()
    for entry in [entry for entry in memo if entry[0] == alias and str(entry[1]).startswith(prefixes)]:
        del memo[entry]


# ---------------------------------------------------------------------------
# Fault tolerant accessors, for the default cache or a named alias
# ---------------------------------------------------------------------------

def _cache_call(operation, default, *args, alias="default", **kwargs):
    try:
        backend = cache if alias == "default" else caches[alias]
        return getattr(backend, operation)(*args, **kwargs)
    except Exception as exc:
        # a failing cache must not propagate: the caller either has a database
        # fallback (reads) or has already written to the database (writes)
        logger.warning("cache %s failed on %s: %s", operation, args[:1], exc)
        return default


def cache_get(key, default=None, alias="default"):
    """``cache.get``, returning ``default`` when the backend is unavailable."""
    if not cache_is_coherent(alias):
        return _memo().get((alias, key), default)
    return _cache_call("get", default, key, default, alias=alias)


def cache_get_many(keys, alias="default"):
    """``cache.get_many``, returning ``{}`` when the backend is unavailable."""
    if not cache_is_coherent(alias):
        memo = _memo()
        return {key: memo[(alias, key)] for key in keys if (alias, key) in memo}
    return _cache_call("get_many", {}, keys, alias=alias)


def cache_set(key, value, timeout=_UNSET, alias="default"):
    """``cache.set``; returns False when the backend is unavailable."""
    if not cache_is_coherent(alias):
        _memo()[(alias, key)] = value
        return True
    return _cache_call("set", False, key, value, timeout=_timeout(timeout), alias=alias) is not False


def cache_set_many(mapping, timeout=_UNSET, alias="default"):
    """``cache.set_many``; returns False when the backend is unavailable."""
    if not mapping:
        return True
    if not cache_is_coherent(alias):
        _memo().update({(alias, key): value for key, value in mapping.items()})
        return True
    return _cache_call("set_many", False, mapping, timeout=_timeout(timeout), alias=alias) is not False


def cache_delete(key, alias="default"):
    """``cache.delete``; returns False when the backend is unavailable."""
    _memo().pop((alias, key), None)
    return _cache_call("delete", False, key, alias=alias) is not False


def cache_delete_many(keys, alias="default"):
    """``cache.delete_many``; returns False when the backend is unavailable."""
    keys = list(keys)
    if not keys:
        return True
    memo = _memo()
    for key in keys:
        memo.pop((alias, key), None)
    return _cache_call("delete_many", False, keys, alias=alias) is not False
