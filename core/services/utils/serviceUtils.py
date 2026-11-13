import json
from typing import Union

from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.forms.models import model_to_dict


def check_authentication(function):
    def wrapper(self, *args, **kwargs):
        if type(self.user) is AnonymousUser or not self.user.id:
            return {
                "success": False,
                "message": "Authentication required",
                "detail": "PermissionDenied",
            }
        else:
            result = function(self, *args, **kwargs)
            return result

    return wrapper


def check_permissions(permissions=None, access_requirements=None):
    """
    `access_requirements` is the optional business map used to fall back on the
    UserBusinessAccess links when the global rights are not enough. It is either the
    map itself (`['location.healthfacility', hf_uuid]`) or a callable receiving the
    decorated call `(self, *args, **kwargs)` and returning it.
    """
    def decorator(function):
        def wrapper(self, *args, **kwargs):
            business_map = access_requirements
            if callable(business_map):
                business_map = business_map(self, *args, **kwargs)
            # only core.User accepts the business map, keep the plain call for the other user types
            has_perms = (self.user.has_perms(permissions, access_requirements=business_map)
                         if business_map else self.user.has_perms(permissions))
            if not has_perms:
                return {
                    "success": False,
                    "message": "Permissions required",
                    "detail": "PermissionDenied",
                }
            else:
                result = function(self, *args, **kwargs)
                return result

        return wrapper

    return decorator


def model_representation(model):
    uuid_string = str(model.id)
    dict_representation = model_to_dict(model)
    dict_representation["id"], dict_representation["uuid"] = (str(uuid_string), str(uuid_string))
    return dict_representation


def output_exception(model_name, method, exception):
    return {
        "success": False,
        "message": f"Failed to {method} {model_name}",
        "detail": str(exception),
        "data": "",
    }


def output_result_success(dict_representation):
    return {
        "success": True,
        "message": "Ok",
        "detail": "",
        "data": json.loads(json.dumps(dict_representation, cls=DjangoJSONEncoder)),
    }


def build_delete_instance_payload():
    return {
        "success": True,
        "message": "Ok",
        "detail": "",
    }


def get_generic_type(generic_type: Union[str, ContentType]):
    if isinstance(generic_type, ContentType):
        return generic_type
    return ContentType.objects.get(model__iexact=str(generic_type).lower())
