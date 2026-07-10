from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from accounts.serializers import SignupSerializer, UserSerializer, WorkspaceSummarySerializer
from accounts.services import register_user
from audit.services import record_audit
from workspaces.models import WorkspaceMember


def _token_pair(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class AuditedLoginView(TokenObtainPairView):
    """TokenObtainPairView + an audit record on failed credentials.

    Behaviour is unchanged (401 on bad credentials); we only observe. The email
    is recorded to support abuse investigation; the password never is.
    """

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except (AuthenticationFailed, InvalidToken):
            record_audit(
                action="auth.login_failed",
                actor=None,
                metadata={
                    "email": str(request.data.get("email", ""))[:254],
                    "reason": "invalid_credentials",
                },
            )
            raise


class SignupView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if not settings.SIGNUP_ENABLED:
            return Response(
                {"detail": "Signup is disabled."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = register_user(**serializer.validated_data)
        return Response(
            {"user": UserSerializer(user).data, "tokens": _token_pair(user)},
            status=status.HTTP_201_CREATED,
        )


class MeView(APIView):
    def get(self, request):
        memberships = WorkspaceMember.objects.filter(user=request.user).select_related("workspace")
        workspaces = [
            {
                "id": m.workspace_id,
                "name": m.workspace.name,
                "type": m.workspace.type,
                "role": m.role,
            }
            for m in memberships
        ]
        return Response(
            {
                "user": UserSerializer(request.user).data,
                "workspaces": WorkspaceSummarySerializer(workspaces, many=True).data,
            }
        )
