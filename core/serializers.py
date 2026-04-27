from django.core.cache import cache
from rest_framework import serializers

from .apps import CoreConfig
from .models import InteractiveUser, Officer, Role, TechnicalUser, User
from claim.models import ClaimAdmin
from location.models import Location
from core.utils import get_cache_key


class CachedModelSerializer(serializers.ModelSerializer):
    cache_ttl = None  # Default cache TTL (infinites)

    def to_representation(self, instance):
        cache_key = get_cache_key(instance.__class__, instance.id)
        cached_data = cache.get(cache_key)

        if cached_data is not None:
            instance = cached_data

        representation = super().to_representation(instance)
        cache.set(cache_key, representation, self.cache_ttl)
        return representation


class RoleSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ("id", "name")


class LocationSerializer(serializers.ModelSerializer):
    parent = serializers.SerializerMethodField()

    class Meta:
        model = Location
        fields = ("id", "uuid", "code", "name", "type", "parent")

    def get_parent(self, obj):
        if not obj or not getattr(obj, "parent", None):
            return None
        return LocationSerializer(obj.parent).data


class PricelistSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    uuid = serializers.CharField(read_only=True)


class ProgramSerializer(serializers.Serializer):
    idProgram = serializers.IntegerField(read_only=True)
    nameProgram = serializers.CharField(read_only=True)


class HealthFacilitySerializer(serializers.ModelSerializer):
    services_pricelist = PricelistSummarySerializer(read_only=True)
    items_pricelist = PricelistSummarySerializer(read_only=True)
    location = LocationSerializer(read_only=True)

    program = ProgramSerializer(many=True, read_only=True)

    class Meta:
        model = ClaimAdmin._meta.get_field("health_facility").related_model
        fields = (
            "id",
            "uuid",
            "code",
            "name",
            "level",
            "services_pricelist",
            "items_pricelist",
            "program",
            "contract_start_date",
            "contract_end_date",
            "location",
        )


class OfficerSerializer(serializers.ModelSerializer):
    location = LocationSerializer(read_only=True)

    class Meta:
        model = Officer
        fields = (
            "id",
            "uuid",
            "code",
            "dob",
            "address",
            "last_name",
            "other_names",
            "location",
        )


class ClaimAdminSerializer(serializers.ModelSerializer):
    health_facility = HealthFacilitySerializer(read_only=True)

    class Meta:
        model = ClaimAdmin
        fields = (
            "id",
            "uuid",
            "code",
            "has_login",
            "email_id",
            "phone",
            "dob",
            "last_name",
            "other_names",
            "health_facility",
        )


class InteractiveUserSerializer(serializers.ModelSerializer):
    language = serializers.PrimaryKeyRelatedField(many=False, read_only=True)
    has_password = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()

    def get_has_password(self, obj):
        return obj.stored_password != CoreConfig.locked_user_password_hash

    def get_roles(self, obj):
        user_roles = getattr(obj, "user_roles", None)

        if not user_roles:
            return []

        user_roles = user_roles.filter(validity_to__isnull=True).select_related("role")

        role_ids = []
        for user_role in user_roles:
            if user_role.role_id not in role_ids:
                role_ids.append(user_role.role_id)

        roles = Role.objects.filter(id__in=role_ids, validity_to__isnull=True)
        role_map = {r.id: r for r in roles}

        ordered_roles = [role_map[rid] for rid in role_ids if rid in role_map]

        return RoleSummarySerializer(ordered_roles, many=True).data

    class Meta:
        model = InteractiveUser
        fields = (
            "id",
            "language",
            "last_name",
            "other_names",
            "health_facility_id",
            "rights",
            "email",
            "phone",
            "roles",
            "has_password",
        )


class TechnicalUserSerializer(serializers.ModelSerializer):
    cache_ttl = 60 * 60

    class Meta:
        model = TechnicalUser
        fields = ("id", "language", "username", "email")


class UserSerializer(serializers.ModelSerializer):
    i_user = InteractiveUserSerializer(many=False, read_only=True)
    t_user = TechnicalUserSerializer(many=False, read_only=True)
    claim_admin = ClaimAdminSerializer(read_only=True)
    officer = OfficerSerializer(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "i_user",
            "t_user",
            "claim_admin",
            "officer",
        )