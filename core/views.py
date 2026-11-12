import csv

from django.http import Http404, StreamingHttpResponse
from django.views.decorators.http import require_GET
from isodate import strftime
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import User, ExportableQueryModel
from .scheduler import scheduler
from .serializers import UserSerializer
from django.utils.translation import gettext as _
from program import models as program_models


def check_user_rights(rights):
    """Permission class requiring `rights` on top of authentication (ported from the
    openIMIS reference core). `rights` may be a list or a zero-argument callable returning
    one, looked up on each request. Cameroon `has_perms` is AND over the list."""

    class UserWithRights(IsAuthenticated):
        def has_permission(self, request, view):
            wanted = rights() if callable(rights) else rights
            return super().has_permission(request, view) and request.user.has_perms(wanted)

    return UserWithRights


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer

    # The right depends on the action (reference core `_ACTION_RIGHTS`). `IsAuthenticated`
    # alone opened a full ModelViewSet on `core.User` to every logged-in account: listing
    # every account, and create / rename / delete of any of them.
    # `current_user` stays open to any authenticated caller: it only returns the caller's
    # own row and the frontend calls it on every login.
    _ACTION_RIGHTS = {
        "list": "gql_query_users_perms",
        "retrieve": "gql_query_users_perms",
        "create": "gql_mutation_create_users_perms",
        "update": "gql_mutation_update_users_perms",
        "partial_update": "gql_mutation_update_users_perms",
        "destroy": "gql_mutation_delete_users_perms",
    }

    def get_permissions(self):
        from core.apps import CoreConfig
        right_attr = self._ACTION_RIGHTS.get(getattr(self, "action", None))
        if right_attr is None:
            return [IsAuthenticated()]
        return [check_user_rights(getattr(CoreConfig, right_attr))()]

    @action(detail=False)
    def current_user(self, request):
        serializer = self.get_serializer(request.user, many=False)
        user_id = request.user._u.id
        programs = program_models.Program.objects.filter(user__id=user_id).filter(
            nameProgram="VIH")
        rights = serializer.data['i_user']['rights']
        if programs and 10119 not in rights:
            rights.append(10119)
        return Response(serializer.data)


@api_view(['GET'])
@require_GET
def fetch_export(request):
    requested_export = request.query_params.get('export')
    export = ExportableQueryModel.objects.filter(name=requested_export).first()
    if not export:
        raise Http404
    elif export.user != request.user:
        raise PermissionDenied({"message": _("Only user requesting export can fetch request")})
    elif export.is_deleted:
        return Response(data='Export csv file was removed from server.', status=status.HTTP_410_GONE)

    export_file_name = F"export_{export.model}_{strftime(export.create_date, '%d_%m_%Y')}.csv"
    return StreamingHttpResponse(
        (row for row in export.content.file.readlines()),
        content_type="text/csv",
        headers={'Content-Disposition': F'attachment; filename="{export_file_name}"'},
    )


def _serialize_job(job):
    return "name: %s, trigger: %s, next run: %s, handler: %s" % (
        job.name, job.trigger, job.next_run_time, job.func)


# An anonymous caller got the name, the trigger, the next run and the handler import path
# of every scheduled job. Cameroon declares no scheduled-jobs right: authentication only.
@api_view(['GET'])
@require_GET
@permission_classes([IsAuthenticated])
def get_scheduled_jobs(request):
    return Response([_serialize_job(job) for job in scheduler.get_jobs()])
