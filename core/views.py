from program import models as program_models
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import User
from .serializers import UserSerializer


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    # If we don't specify the IsAuthenticated, the framework will look for the core.user_view permission and prevent
    # any access from non-admin users
    permission_classes = [IsAuthenticated]

    @action(detail=False)
    def current_user(self, request):
        serializer = self.get_serializer(request.user, many=False)
        response = serializer.data
        user_id = request.user._u.id
        programs = program_models.Program.objects.filter(user__id=user_id).filter(
            nameProgram="VIH")
        if programs:
            response['i_user']['rights'].append(10119)
        return Response(response)
