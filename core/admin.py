from django import forms
from django.contrib import admin
from django.contrib.auth.models import Group, Permission
from .models import FieldControl, ModuleConfiguration, TechnicalUser, UserBusinessAccess
from .forms import TechnicalUserAdmin, GroupAdmin
from .uba_link_types import uba_link_type_choices

admin.site.unregister(Group)

admin.site.register(FieldControl)
admin.site.register(ModuleConfiguration)
admin.site.register(TechnicalUser, TechnicalUserAdmin)
admin.site.register(Permission)
admin.site.register(Group, GroupAdmin)


class UbaLinkTypeListFilter(admin.SimpleListFilter):
    """Filter on the registered link types rather than on the raw codes stored in rows."""
    title = "link type"
    parameter_name = "link_type"

    def lookups(self, request, model_admin):
        return uba_link_type_choices()

    def queryset(self, request, queryset):
        return queryset.filter(link_type=self.value()) if self.value() else queryset


@admin.register(UserBusinessAccess)
class UserBusinessAccessAdmin(admin.ModelAdmin):
    list_display = ['user', 'link_type', 'content_type', 'object_id', 'date_valid_from', 'date_valid_to', 'active']
    list_filter = [UbaLinkTypeListFilter, 'content_type', 'active']
    search_fields = ['user__username', 'object_id']
    raw_id_fields = ['user']

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """
        Turn link_type into a picker fed by the registry. Done here rather than with
        `choices` on the model field: a module registering a new type would otherwise
        make makemigrations emit a migration, and django 4.2 does not take a callable.
        """
        if db_field.name == "link_type":
            kwargs["widget"] = forms.Select(choices=[("", "---------")] + uba_link_type_choices())
        return super().formfield_for_dbfield(db_field, request, **kwargs)
