"""
Tests for backend/features/cache-performance.feature (object cache rules of core.utils.CachedManager).

No Cameroon model of this checkout uses the object cache by default, so the tests bind a
CachedManager to Product (the model of the GlitchTip case cs_Product_107) with USE_CACHE on.
"""
import unittest
from unittest import mock

from django.test import TestCase

from core import utils as core_utils
from core.utils import CachedManager, CachedModelMixin, clean_fk, get_cache_key
from product.models import Product
from product.test_helpers import create_test_product


class _CachedProductManager(CachedManager):
    pass


class CacheRulesTest(TestCase):
    def setUp(self):
        patcher = mock.patch.object(Product, "USE_CACHE", True, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        # the DB fallback of CachedManager.get() calls instance.update_cache() (CachedModelMixin)
        hook = mock.patch.object(Product, "update_cache", CachedModelMixin.update_cache, create=True)
        hook.start()
        self.addCleanup(hook.stop)
        core_utils.cache.clear()
        self.addCleanup(core_utils.cache.clear)
        self.manager = _CachedProductManager()
        self.manager.model = Product

    def _cache_primary(self, product):
        core_utils.cache.set(get_cache_key(Product, product.pk), clean_fk(product))

    # Scenario: A secondary key redirecting to the primary key is followed (@redmine-34242)
    def test_secondary_key_redirect(self):
        for kind, convert in (("int", int), ("string", str)):
            with self.subTest(type=kind):
                product = create_test_product("FCACHE")
                self._cache_primary(product)
                core_utils.cache.set(get_cache_key(Product, product.uuid), convert(product.pk))
                with self.assertNoLogs(core_utils.logger, level="ERROR"):
                    with self.assertNumQueries(0):
                        found = self.manager.get(uuid=product.uuid)
                self.assertEqual(found.pk, product.pk)
                core_utils.cache.clear()

    # Scenario: A cached object with another UUID is ignored (@redmine-38072 @redmine-38092)
    def test_uuid_mismatch_falls_back_to_db(self):
        product_a = create_test_product("FCACHA")
        product_b = create_test_product("FCACHB")
        self._cache_primary(product_b)
        core_utils.cache.set(get_cache_key(Product, product_a.uuid), str(product_b.pk))
        found = self.manager.get(uuid=product_a.uuid)
        self.assertEqual(str(found.uuid).lower(), str(product_a.uuid).lower(),
                         "the cache returned product B for the uuid of product A (core PR 78/80/81 not here)")

    # Scenario: Reading an object after an update returns the new values (@redmine-33592 @redmine-33738)
    def test_update_refreshes_cache(self):
        product = create_test_product("FCACHU", custom_props={"age_maximal": 50})
        CachedModelMixin.update_cache(product)
        self.assertEqual(self.manager.get(id=product.id).age_maximal, 50)
        product.age_maximal = 46
        product.save()
        CachedModelMixin.update_cache(product)  # what CachedModelMixin models do on save()
        with self.assertNumQueries(0):
            self.assertEqual(self.manager.get(id=product.id).age_maximal, 46)


class OpenCacheScenariosTest(TestCase):
    # Scenario: Module configurations are read from the cache with a database fallback (@redmine-37346)
    @unittest.skip("postponed: #37346")
    def test_module_configuration_cache(self):
        pass

    # Scenario: More objects and filters are cached (@redmine-36107 @redmine-36493)
    @unittest.skip("open: #36493")
    def test_more_objects_cached(self):
        pass

    # Scenario: Master data is served from the cache (@redmine-36495)
    @unittest.skip("open: #36495")
    def test_master_data_from_cache(self):
        pass
