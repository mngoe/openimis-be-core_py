import os
from django.test import TestCase
from django.db import connections
from django.test.runner import DiscoverRunner
from django.test.utils import get_unique_databases_and_mirrors

from .utils import full_class_name, comparable


class ComparableTest(TestCase):
    def test_generic_eq(self):
        @comparable
        class A(object):
            def __init__(self, f):
                self.f = f

            def __eq__(self, other):
                return self.f == other.f

        @comparable
        class B(object):
            def __init__(self, f):
                self.f = f

            def __eq__(self, other):
                return self.f == other.f

        obj1 = A(f='a')
        obj2 = A(f='a')
        self.assertEquals(obj1, obj2)
        obj3 = B(f='b')
        self.assertNotEquals(obj1, obj3)
        obj4 = B(f='a')
        self.assertNotEquals(obj1, obj4)


class UtilsTestCase(TestCase):
    def test_full_class_name(self):
        self.assertEquals(full_class_name(
            self), 'core.test_utils.UtilsTestCase')

        self.assertEquals(full_class_name(
            1), 'int')

    def test_cache_invalidation(self):
        from core.models import User
        User.USE_CACHE = True
        from django.core.cache import caches
        users = list(User.objects.all())
        users_id = [user.id for user in users]
        users_0_no_cache_get = User.objects.get(id=users_id[0])
        users_0_filter = User.objects.filter(id=users_id[0]).first()
        self.assertEquals(users_0_no_cache_get, users_0_filter, "get and filter should retrieve the same object")
        users_0_filter.username = users_0_filter.username + 'T'
        users_0_filter.save()
        users_filter = list(User.objects.filter(id__in=users_id))
        caches['default'].delete(f"cd_User_{users_filter[2].id}")
        users.remove(users_0_no_cache_get)
        users_filter.remove(users_0_filter)
        users_0_filter = User.objects.filter(id=users_id[0]).first()
        self.assertNotEquals(users_0_no_cache_get.username, users_0_filter.username, "the object should be different, cache not invalidated properly")
        self.assertNotEquals(users, users_filter, "should be the same list even if user_filter comes partially from cache")
        caches['default'].clear()
        User.USE_CACHE = False
