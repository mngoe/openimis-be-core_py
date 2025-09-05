import uuid
from copy import copy
from datetime import datetime as py_datetime
from django.core.cache import caches
from django.db import models
#from core.datetimes.ad_datetime import datetime as py_datetime

from ..fields import DateTimeField
from ..utils import filter_validity, get_cache_key
from django.db.models import Q
from django.db.models.query import QuerySet
import logging

logger = logging.getLogger(__name__)

cache = caches["default"]


class CachedManager(models.Manager):
    UNIQUE_FIELDS = {'pk', 'id', 'uuid'}
    CACHED_FK = {}
    
    def get(self, *args, **kwargs):
        """
        Overrides the get() method to check Redis cache before
        performing a DB lookup for simple unique lookups.
        """
        #print("Get on Cached Manager")
        #print(kwargs)
        #print(args)
        
        unique_fields = ('pk','id','uuid')
        cache_key = None

        # Case 1: Simple kwargs lookup.
        if kwargs and len(kwargs) == 1:
            key = list(kwargs.keys())[0]
            if key in unique_fields:
                value = kwargs[key]
                # Convert UUID objects to string for the cache key.
                if key in ('id', 'pk'):
                    try:
                        # Convert to int if possible.
                        value = int(value)
                    except (ValueError, TypeError):
                        pass
                if isinstance(value, uuid.UUID):
                    value = str(value)
                cache_key = f"{self.model.__name__}:{value}"
                print("cache_key", cache_key)
        # use case for Family Request elements in args
        elif not kwargs and args and len(args) == 1 :
            if len(args[0].children) == 1:
                field, value = args[0].children[0]
                if field in unique_fields:
                    cache_key = f"{self.model.__name__}:{value}"
                    print("cache_key", cache_key)

        # If we constructed a cache key, try to retrieve from the cache.
        if cache_key:
            cached_instance = cache.get(cache_key)
            print("cached_instance", cached_instance)
            if cached_instance is not None:
                print("Returning cached instance for key: %s", cache_key)
                logger.debug("Returning cached instance for key: %s", cache_key)
                return cached_instance

            # Not in cache; perform DB lookup.
            instance = super().get(*args, **kwargs)
            cache.set(cache_key, instance, timeout=None)
            print("Cached instance %s after DB lookup", cache_key)
            logger.debug("Cached instance %s after DB lookup", cache_key)
            return instance

        # Fallback: if the lookup is not a simple unique one, use the default get().
        return super().get(*args, **kwargs)
    
    
    def _normalize_value(self, value):
        """Normalize value for cache key."""
        if isinstance(value, uuid.UUID):
            return str(value)
        try:
            return int(value)
        except (ValueError, TypeError):
            return value


    def _is_simple_lookup(self, args, kwargs):
        """Check if query is a single exact or in lookup on unique fields."""
        if kwargs and len(kwargs) == 1:
            key = list(kwargs.keys())[0]
            field = key.split('__')[0] if '__' in key else key
            lookup = key.split('__')[-1] if '__' in key else 'exact'
            return field in self.UNIQUE_FIELDS and lookup in {'exact', 'in'}, key, kwargs.get(key), field, lookup
        elif args and len(args) == 1 and isinstance(args[0], Q):
            if len(args[0].children) == 1 and isinstance(args[0].children[0], tuple):
                field, value = args[0].children[0]
                lookup = field.split('__')[-1] if '__' in field else 'exact'
                field = field.split('__')[0]
                return field in self.UNIQUE_FIELDS and lookup in {'exact', 'in'}, field, value, field, lookup
        return False, None, None, None, None


    def _instances_to_queryset(self, instances):
        """Convert a list of model instances to a QuerySet without hitting the database."""
        if not instances:
            return self.get_queryset().none()
        qs = self.get_queryset().filter(pk__in=[instance.pk for instance in instances])
        qs._result_cache = list(instances)
        return qs


    def _handle_cache_lookup(self, field, value, lookup):
        """Handle cache lookup for exact or in queries."""
        if lookup == 'exact':
            cache_key = get_cache_key(self.model, self._normalize_value(value))
            cached_instance = cache.get(cache_key)
            if cached_instance:
                if not isinstance(cached_instance, self.model):
                    logger.error("Wrong model for cached instance: %s", cache_key)
                    return None
                logger.debug("Cache hit for key: %s", cache_key)
                return self._instances_to_queryset([cached_instance])
            return None

        if lookup == 'in':
            if not isinstance(value, (list, tuple, set)):
                return None
            values = [self._normalize_value(v) for v in value]
            cache_keys = [get_cache_key(self.model, v) for v in values]
            cached_results = cache.get_many(cache_keys)
            cached_instances = []
            uncached_values = []

            for v, ck in zip(values, cache_keys):
                instance = cached_results.get(ck)
                if instance:
                    if isinstance(instance, self.model):
                        cached_instances.append(instance)
                        logger.debug("Cache hit for key: %s", ck)
                    else:
                        logger.error("Wrong model for cached instance: %s", ck)
                        uncached_values.append(v)
                else:
                    uncached_values.append(v)

            qs = self.get_queryset().none()
            if cached_instances:
                qs = self._instances_to_queryset(cached_instances)
            return qs, uncached_values, field

        return None


    def filter(self, *args, **kwargs):
        """
        Overrides filter() to use cache for single exact or in lookups on pk, id, or uuid.
        Returns a QuerySet to support chaining without unnecessary DB queries.
        """
        is_simple, key, value, field, lookup = self._is_simple_lookup(args, kwargs)
        if not is_simple:
            return super().filter(*args, **kwargs)

        # Try cache lookup
        cache_result = self._handle_cache_lookup(field, value, lookup)
        if cache_result is None:
            # Fallback to default filter for invalid lookups or cache miss
            return super().filter(*args, **kwargs)

        if lookup == 'exact':
            cached_qs = cache_result
            if cached_qs is not None:
                return cached_qs
            # Cache miss, query DB and cache
            qs = super().filter(*args, **kwargs)
            if qs.exists():
                instance = qs.first()
                cache.set(get_cache_key(self.model, self._normalize_value(value)), instance, timeout=None)
                logger.debug("Cached instance %s after DB lookup", get_cache_key(self.model, value))
            return qs

        # Handle in lookup
        cached_qs, uncached_values, field = cache_result
        if uncached_values:
            db_filter = {f"{field}__in": uncached_values}
            if kwargs:
                db_qs = super().filter(**{f"{field}__in": uncached_values})
            else:
                db_qs = super().filter(Q(**{f"{field}__in": uncached_values}))
            for instance in db_qs:
                cache_key = get_cache_key(self.model, self._normalize_value(getattr(instance, field)))
                cache.set(cache_key, instance, timeout=None)
                logger.debug("Cached instance %s after DB lookup", cache_key)
            cached_qs = cached_qs | db_qs

        return cached_qs


    def get_from_cache(self, **kwargs):
        """
        Utility method to fetch instances from cache or DB using ORM-like syntax.
        Returns a QuerySet.
        """
        is_simple, key, value, field, lookup = self._is_simple_lookup((), kwargs)
        if not is_simple:
            return self.filter(**kwargs)

        cache_result = self._handle_cache_lookup(field, value, lookup)
        if cache_result is None:
            return self.filter(**kwargs)

        if lookup == 'exact':
            cached_qs = cache_result
            if cached_qs is not None:
                return cached_qs
            qs = self.filter(**kwargs)
            if qs.exists():
                cache.set(get_cache_key(self.model, self._normalize_value(value)), qs.first(), timeout=None)
            return qs

        cached_qs, uncached_values, field = cache_result
        if uncached_values:
            db_qs = self.filter(**{f"{field}__in": uncached_values})
            for instance in db_qs:
                cache_key = get_cache_key(self.model, self._normalize_value(getattr(instance, field)))
                cache.set(cache_key, instance, timeout=None)
            cached_qs = cached_qs | db_qs

        return cached_qs

class BaseVersionedModel(models.Model):
    validity_from = DateTimeField(db_column='ValidityFrom', default=py_datetime.now)
    validity_to = DateTimeField(db_column='ValidityTo', blank=True, null=True)

    # Use our custom CachedManager for object retrieval
    objects = CachedManager()

    def save(self, *args, **kwargs):
        """
        Overrides the default save to update the cache after saving the instance.
        """
        # First, perform the DB save.
        super().save(*args, **kwargs)

        # Build the cache key using the same logic as in the CachedManager.
        # (Assuming lookups are done using pk/id/uuid)
        cache_key = f"{self.__class__.__name__}:{self.pk}"
        # Optionally, check for a uuid attribute:
        # if hasattr(self, "uuid") and self.uuid:
        #     cache_key = f"{self.__class__.__name__}:{str(self.uuid)}"
        
        # Update the cache with the latest version.
        cache.set(cache_key, self, timeout=None)
        print("Saved and cached instance: %s", cache_key)
        logger.debug("Saved and cached instance: %s", cache_key)
        return self

    def delete(self, *args, **kwargs):
        """
        Overrides the default delete to remove the instance from the cache.
        """
        # Build the cache key prior to deletion.
        cache_key = f"{self.__class__.__name__}:{self.pk}"
        # Delete the cache entry.
        cache.delete(cache_key)
        logger.debug("Removed instance from cache: %s", cache_key)
        # Then perform the actual deletion.
        return super().delete(*args, **kwargs)

    def save_history(self, **kwargs):
        if not self.id:  # only copy if the data is being updated
            return None
        histo = copy(self)
        histo.id = None
        if hasattr(histo, "uuid"):
            setattr(histo, "uuid", uuid.uuid4())
        histo.validity_to = py_datetime.now()
        histo.legacy_id = self.id
        histo.save()
        return histo.id

    def delete_history(self, **kwargs):
        self.save_history()
        now = py_datetime.now()
        self.validity_from = now
        self.validity_to = now
        self.save()

    class Meta:
        abstract = True

    @classmethod
    def filter_queryset(cls, queryset=None):
        if queryset is None:
            queryset = cls.objects.all()
        queryset = queryset.filter(*filter_validity())
        return queryset


class VersionedModel(BaseVersionedModel):
    legacy_id = models.IntegerField(
        db_column='LegacyID', blank=True, null=True)

    class Meta:
        abstract = True


class UUIDVersionedModel(BaseVersionedModel):
    legacy_id = models.UUIDField(
        db_column='LegacyID', blank=True, null=True)

    class Meta:
        abstract = True


