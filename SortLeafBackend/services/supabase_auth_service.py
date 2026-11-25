"""
Authentication service using Supabase Auth.
"""
import logging
from typing import Optional
from uuid import UUID

from gotrue.errors import AuthApiError

from supabase import Client
from models.user_models import (
    UserRegisterRequest,
    UserLoginRequest,
    RefreshTokenRequest,
    ResendVerificationRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    UserResponse,
    UserProfile,
    TokenResponse,
    AuthResponse,
    MessageResponse,
    ProfilePictureResponse
)
from exceptions.base_exception import SortLeafException
from exceptions.auth_exceptions import (
    AuthenticationError,
    UserNotFoundError,
    InvalidTokenError,
    InactiveUserError
)
from config.settings import ENVIRONMENT, FRONTEND_URL

logger = logging.getLogger(__name__)

# Remove trailing slash from FRONTEND_URL to avoid double slashes in redirects
_FRONTEND_URL = FRONTEND_URL.rstrip('/')

def email_callback(path: str) -> str:
    if ENVIRONMENT == "production":
        origin = _FRONTEND_URL or "https://sortleaf.vercel.app"
        if not origin.startswith("https://"):
            raise SortLeafException("verification_unavailable", 503)
        return origin + path
    return (_FRONTEND_URL or "http://localhost:5173") + path

def verification_error(error: AuthApiError) -> SortLeafException:
    if error.status == 429:
        return SortLeafException("verification_rate_limited", 429)
    if error.status >= 500 or error.code in {"email_address_not_authorized", "email_provider_disabled"}:
        return SortLeafException("verification_unavailable", 503)
    return SortLeafException("registration_failed", 400)


AVATAR_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}


class SupabaseAuthService:
    """
    Authentication service using Supabase Auth.
    Handles registration, login, logout, and email verification.
    """
    
    def __init__(self, client: Client):
        self.client = client
    
    def register(self, user_data: UserRegisterRequest) -> MessageResponse:
        """Start email verification without issuing an account session."""
        try:
            response = self.client.auth.sign_up({
                "email": user_data.email,
                "password": user_data.password,
                "options": {
                    "data": {
                        "name": user_data.name,
                        "last_name": user_data.last_name,
                        "telephone": user_data.telephone
                    },
                    "email_redirect_to": email_callback("/auth/callback")
                }
            })
            
            if not response.user or response.session:
                logger.error("Supabase signup did not enter the email confirmation flow")
                raise SortLeafException("verification_unavailable", 503)
        except AuthApiError as exc:
            if exc.code not in {"email_exists", "user_already_exists"}:
                raise verification_error(exc) from exc
        return MessageResponse(message="verification_pending")

    def login(self, credentials: UserLoginRequest) -> AuthResponse:
        """
        Authenticate user with email and password.
        
        Args:
            credentials: Login credentials
            
        Returns:
            AuthResponse: User data and authentication tokens
        """
        try:
            response = self.client.auth.sign_in_with_password({
                "email": credentials.email,
                "password": credentials.password
            })
            
            if response.user is None or response.session is None:
                raise AuthenticationError("Invalid email or password")
            
            user = response.user
            session = response.session
            
            if not user.email_confirmed_at:
                raise AuthenticationError("email_not_confirmed")
            
            # Get profile data
            profile = self._get_profile(user.id)
            
            user_response = UserResponse(
                id=UUID(user.id),
                email=user.email,
                email_verified=user.email_confirmed_at is not None,
                profile=profile
            )
            
            tokens = TokenResponse(
                access_token=session.access_token,
                refresh_token=session.refresh_token,
                expires_in=session.expires_in or 3600,
                expires_at=session.expires_at
            )
            
            return AuthResponse(user=user_response, tokens=tokens)
            
        except AuthApiError as e:
            if e.code == "email_not_confirmed":
                raise AuthenticationError("email_not_confirmed") from e
            logger.warning("Login failed")
            raise AuthenticationError("Invalid email or password")
        except Exception as e:
            logger.error(f"Login error: {e}")
            raise
    
    def logout(self, access_token: str) -> MessageResponse:
        """
        Logout user by invalidating the session.
        
        Args:
            access_token: Current access token
        """
        try:
            # Set the session to use this token
            self.client.auth.admin.sign_out(access_token, scope="local")
            logger.info("User logged out")
            return MessageResponse(message="Successfully logged out")
        except Exception as e:
            logger.error(f"Logout error: {e}")
            raise AuthenticationError("Could not end session") from e
    
    def refresh_tokens(self, refresh_token: str) -> TokenResponse:
        """
        Refresh access token using refresh token.
        
        Args:
            refresh_token: Current refresh token
            
        Returns:
            TokenResponse: New token pair
        """
        try:
            response = self.client.auth.refresh_session(refresh_token)
            
            if response.session is None:
                raise InvalidTokenError("Invalid refresh token")
            
            session = response.session
            
            return TokenResponse(
                access_token=session.access_token,
                refresh_token=session.refresh_token,
                expires_in=session.expires_in or 3600,
                expires_at=session.expires_at
            )
            
        except AuthApiError as e:
            logger.warning(f"Token refresh failed: {e}")
            raise InvalidTokenError("Invalid or expired refresh token")
        except Exception as e:
            logger.error(f"Refresh error: {e}")
            raise
    
    def get_current_user(self, access_token: str) -> UserResponse:
        """
        Get the currently authenticated user from access token.
        
        Args:
            access_token: JWT access token
            
        Returns:
            UserResponse: Current user data
        """
        try:
            # Get user from token
            response = self.client.auth.get_user(access_token)
            
            if (not response or not response.user or not response.user.email_confirmed_at
                    or not response.user.email or response.user.is_anonymous):
                raise InvalidTokenError("Invalid token")
            
            user = response.user
            
            # Get profile data
            profile = self._get_profile(user.id)
            
            return UserResponse(
                id=UUID(user.id),
                email=user.email,
                email_verified=user.email_confirmed_at is not None,
                profile=profile
            )
            
        except AuthApiError as e:
            logger.warning(f"Get user failed: {e}")
            raise InvalidTokenError("Invalid or expired token")
        except Exception as e:
            logger.error(f"Get user error: {e}")
            raise
    
    def resend_verification_email(
        self, 
        request: ResendVerificationRequest
    ) -> MessageResponse:
        """
        Resend verification email.
        
        Args:
            request: Contains email address
            
        Returns:
            MessageResponse: Generic success message
        """
        try:
            self.client.auth.resend({
                "type": "signup",
                "email": request.email,
                "options": {
                    "email_redirect_to": email_callback("/auth/callback")
                }
            })
            
        except AuthApiError as exc:
            if exc.code not in {"user_not_found", "email_exists", "user_already_exists"}:
                raise verification_error(exc) from exc
        return MessageResponse(message="verification_pending")

    def forgot_password(
        self, 
        request: ForgotPasswordRequest
    ) -> MessageResponse:
        """
        Send password reset email.
        
        Args:
            request: Contains email address
            
        Returns:
            MessageResponse: Generic success message
        """
        try:
            self.client.auth.reset_password_email(
                request.email,
                options={
                    "redirect_to": email_callback("/auth/reset-password")
                }
            )
            
            return MessageResponse(
                message="If the email exists, a password reset link was sent"
            )
            
        except SortLeafException:
            raise
        except Exception as e:
            logger.error(f"Forgot password error: {e}")
            return MessageResponse(
                message="If the email exists, a password reset link was sent"
            )
    
    def reset_password(
        self, 
        request: ResetPasswordRequest
    ) -> MessageResponse:
        """
        Reset password using token from email.
        
        Args:
            request: Contains access_token and new password
            
        Returns:
            MessageResponse: Success message
        """
        try:
            # The access_token from the reset email URL is a JWT session token
            # We need to set the session with it, then update the password
            # Supabase sends: #access_token=...&refresh_token=...&type=recovery
            
            # Set session using the access token from the recovery email
            # This authenticates the user with the recovery token
            self.client.auth.set_session(
                access_token=request.access_token,
                refresh_token=request.refresh_token
            )
            
            # Now update the password for the authenticated user
            self.client.auth.update_user({
                "password": request.new_password
            })
            
            # Sign out after password change for security
            self.client.auth.sign_out()
            
            return MessageResponse(message="Password updated successfully")
            
        except AuthApiError as e:
            logger.warning(f"Reset password failed: {e}")
            raise InvalidTokenError("Invalid or expired reset token")
        except Exception as e:
            logger.error(f"Reset password error: {e}")
            raise
    
    def update_profile(
        self, 
        user_id: UUID, 
        **kwargs
    ) -> UserProfile:
        """
        Update user profile data.
        
        Args:
            user_id: User's UUID
            **kwargs: Fields to update (name, last_name, telephone, profile_picture)
            
        Returns:
            UserProfile: Updated profile
        """
        try:
            # Filter valid fields
            valid_fields = {'name', 'last_name', 'telephone', 'profile_picture'}
            update_data = {}
            
            for k, v in kwargs.items():
                if k in valid_fields:
                    # Handle empty string as None (for removing profile picture)
                    if v == '':
                        update_data[k] = None
                    else:
                        update_data[k] = v
            
            if not update_data:
                return self._get_profile(str(user_id))
            
            result = self.client.table('profiles')\
                .update(update_data)\
                .eq('id', str(user_id))\
                .execute()
            
            if result.data:
                return UserProfile(**result.data[0])
            
            raise UserNotFoundError()
            
        except Exception as e:
            logger.error(f"Update profile error: {e}")
            raise
    
    def _get_profile(self, user_id: str) -> UserProfile:
        """Get user profile from profiles table."""
        try:
            # Use .execute() without .maybe_single() to avoid 406 errors
            # when the profile doesn't exist yet
            result = self.client.table('profiles')\
                .select('*')\
                .eq('id', user_id)\
                .execute()
            
            if result.data and len(result.data) > 0:
                return UserProfile(**result.data[0])
            
            # Profile doesn't exist yet (trigger may not have run)
            # Return empty profile
            return UserProfile(
                id=UUID(user_id),
                name="",
                last_name="",
                telephone=None,
                profile_picture=None,
                created_at=None
            )
            
        except Exception as e:
            logger.warning(f"Get profile error: {e}")
            return UserProfile(
                id=UUID(user_id),
                name="",
                last_name="",
                telephone=None,
                profile_picture=None,
                created_at=None
            )

    def read_profile_picture(self, user: UserResponse) -> tuple[bytes, str]:
        path = user.profile.profile_picture if user.profile else None
        for mime, extension in AVATAR_EXTENSIONS.items():
            if path == f"{user.id}/avatar.{extension}":
                return self.client.storage.from_("avatars").download(path), mime
        raise UserNotFoundError("Profile picture not found")

    def upload_profile_picture(self, user_id: UUID, file_content: bytes, content_type: str) -> str:
        path = f"{user_id}/avatar.{AVATAR_EXTENSIONS[content_type]}"
        self.client.storage.from_("avatars").upload(
            path=path, file=file_content,
            file_options={"content-type": content_type, "cache-control": "0", "upsert": "true"},
        )
        self.update_profile(user_id, profile_picture=path)
        return path

    def change_password(
        self,
        email: str,
        current_password: str,
        new_password: str
    ) -> MessageResponse:
        """
        Change user password after verifying current password.
        
        Args:
            email: User's email address
            current_password: Current password for verification
            new_password: New password to set
            
        Returns:
            MessageResponse: Success message
        """
        try:
            # First, verify the current password by attempting to sign in
            response = self.client.auth.sign_in_with_password({
                "email": email,
                "password": current_password
            })
            
            if response.user is None or response.session is None:
                raise AuthenticationError("Current password is incorrect")
            
            # Now update the password for the authenticated user
            self.client.auth.update_user({
                "password": new_password
            })
            
            logger.info(f"Password changed for user: {email}")
            
            return MessageResponse(message="Password changed successfully")
            
        except AuthApiError as e:
            logger.warning(f"Change password failed: {e}")
            if "invalid" in str(e).lower() or "password" in str(e).lower():
                raise AuthenticationError("Current password is incorrect")
            raise AuthenticationError(str(e))
        except Exception as e:
            logger.error(f"Change password error: {e}")
            raise
