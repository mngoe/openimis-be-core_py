import uuid
from copy import copy
from datetime import datetime as py_datetime
from django.db import models
from django.db.models import Q
#from core.datetimes.ad_datetime import datetime as py_datetime

from ..fields import DateTimeField
from ..utils import filter_validity
import logging
from core.models.row_security import RowSecurityMixin
from core.models.row_filters import NO_ROWS, Audits, day_bounds

logger = logging.getLogger(__name__)

class BaseVersionedModel(RowSecurityMixin, models.Model):
    validity_from = DateTimeField(db_column='ValidityFrom', default=py_datetime.now)
    validity_to = DateTimeField(db_column='ValidityTo', blank=True, null=True)

    def save_history(self, **kwargs):
        if not self.id:  # only copy if the data is being updated
            return None
        histo = copy(self)
        histo.id = None
        if hasattr(histo, "uuid"):
            setattr(histo, "uuid", uuid.uuid4())
        from core import datetime
        histo.validity_to = datetime.datetime.now()
        histo.legacy_id = self.id
        histo.save()
        return histo.id

    def delete_history(self, **kwargs):
        self.save_history()
        from core import datetime
        now = datetime.datetime.now()
        self.validity_from = now
        self.validity_to = now
        self.save()

    class Meta:
        abstract = True

    #: True on the tables whose versions carry a business meaning (price
    #: lists, products, health facilities...): a validity date then picks the
    #: version valid at that date. Elsewhere versions are an audit trail only.
    business_versioned = False

    @classmethod
    def get_business_validity_fields(cls):
        return ("validity_from", "validity_to") if cls.business_versioned else None

    @classmethod
    def live_rows_q(cls):
        return Q(validity_to__isnull=True)

    @classmethod
    def _has_legacy_id(cls):
        return any(field.name == "legacy_id" for field in cls._meta.fields)

    @classmethod
    def current_rows_q(cls):
        # save_history() copies the row with legacy_id pointing at it;
        # delete_history() closes validity_to on the row itself.
        return Q(legacy_id__isnull=True) if cls._has_legacy_id() else None

    @classmethod
    def dated_rows_q(cls):
        # The version valid at a date may be an audit copy by now.
        return Q()

    @classmethod
    def audit_rows_q(cls, audits):
        if not cls._has_legacy_id():
            return NO_ROWS
        copies = Q(legacy_id__isnull=False)
        if audits == Audits.ALL:
            return copies
        start, end = day_bounds(*audits.range())
        if audits.kind == "at":
            return copies & Q(validity_from__lte=end, validity_to__gte=start)
        # An audit copy is dated by the change that superseded it.
        if start is not None:
            copies &= Q(validity_to__gte=start)
        if end is not None:
            copies &= Q(validity_to__lte=end)
        return copies

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


