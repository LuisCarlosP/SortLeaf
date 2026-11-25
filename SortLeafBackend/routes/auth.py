"""
Authentication routes using Supabase Auth - Register, Login, Logout, Email Verification.
"""
import logging
from fastapi import Request, Response
from core.rate_limit import limiter
from core.identity import scoped_client
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, status, Header, HTTPException, UploadFile, File

from models.user_models import (
    UserRegisterRequest,
    UserLoginRequest,
    RefreshTokenRequest,
    ResendVerificationRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    UpdateProfileRequest,
    ChangePasswordRequest,
    AuthResponse,
    TokenResponse,
    MessageResponse,
    UserResponse,
    UserProfile,
    ProfilePictureResponse
)
from services.supabase_auth_service import AVATAR_EXTENSIONS, SupabaseAuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])




@router.post("/guest", response_model=TokenResponse)
@limiter.limit("10/hour")
def start_guest_session(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        with scoped_client() as db:
            session = db.auth.sign_in_anonymously().session
    except Exception as exc:
        raise HTTPException(503, "guest_session_unavailable") from exc
    if not session:
        raise HTTPException(503, "guest_session_unavailable")
    return TokenResponse(access_token=session.access_token, refresh_token=session.refresh_token,
                         expires_in=session.expires_in, expires_at=session.expires_at)


def get_auth_service(authorization: str | None = Header(None)):
    token = extract_bearer_token(authorization) if authorization else None
    with scoped_client(token) as db:
        yield SupabaseAuthService(db)


def extract_bearer_token(authorization: str) -> str:
    """Extract token from Authorization header."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing"
        )
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Use: Bearer <token>"
        )
    
    return parts[1]


@router.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Register a new user",
    description="""
    Create a new user account with Supabase Auth.
    
    **A verification email will be sent automatically.**
    
    **Password Requirements:**
    - Minimum 8 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    
    **Optional Fields:**
    - telephone: Phone number (8-15 digits)
    """
)
def register(
    user_data: UserRegisterRequest,
    auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> MessageResponse:
    """Register a new user account. Verification email sent automatically."""
    return auth_service.register(user_data)


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="User login",
    description="Authenticate with email and password to receive tokens."
)
def login(
    credentials: UserLoginRequest,
    auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> AuthResponse:
    """Authenticate user and return tokens."""
    return auth_service.login(credentials)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="User logout",
    description="""
    Logout the current user and invalidate the session.
    
    **Headers Required:**
    - Authorization: Bearer <access_token>
    """
)
def logout(
    authorization: str = Header(..., description="Bearer access_token"),
    auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> MessageResponse:
    """Logout user by invalidating session."""
    access_token = extract_bearer_token(authorization)
    return auth_service.logout(access_token)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh tokens",
    description="""
    Get new access and refresh tokens using a valid refresh token.
    """
)
def refresh_tokens(
    token_request: RefreshTokenRequest,
    auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> TokenResponse:
    """Refresh the access token using refresh token."""
    return auth_service.refresh_tokens(token_request.refresh_token)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
    description="Get the profile of the currently authenticated user."
)
def get_current_user(
    authorization: str = Header(..., description="Bearer access_token"),
    auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> UserResponse:
    """Get current authenticated user's profile."""
    token = extract_bearer_token(authorization)
    return auth_service.get_current_user(token)


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    summary="Resend verification email",
    description="Resend the verification email to the user's address."
)
def resend_verification(
    request: ResendVerificationRequest,
    auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> MessageResponse:
    """Resend verification email."""
    return auth_service.resend_verification_email(request)


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    summary="Request password reset",
    description="Send a password reset email to the user's address."
)
def forgot_password(
    request: ForgotPasswordRequest,
    auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> MessageResponse:
    """Send password reset email."""
    return auth_service.forgot_password(request)


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Reset password",
    description="Reset password using the token from the email link."
)
def reset_password(
    request: ResetPasswordRequest,
    auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> MessageResponse:
    """Reset user password with token."""
    return auth_service.reset_password(request)


@router.patch(
    "/profile",
    response_model=UserProfile,
    summary="Update user profile",
    description="Update the current user's profile information."
)
def update_profile(
    profile_data: UpdateProfileRequest,
    authorization: str = Header(..., description="Bearer access_token"),
    auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> UserProfile:
    """Update user profile data."""
    token = extract_bearer_token(authorization)
    user = auth_service.get_current_user(token)
    return auth_service.update_profile(
        user.id,
        **profile_data.model_dump(exclude_unset=True)
    )


@router.get(
    "/validate",
    response_model=MessageResponse,
    summary="Validate token",
    description="Check if the current access token is valid."
)
def validate_token(
    authorization: str = Header(..., description="Bearer access_token"),
    auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> MessageResponse:
    """Validate the current access token."""
    token = extract_bearer_token(authorization)
    
    try:
        auth_service.get_current_user(token)
        return MessageResponse(message="Token is valid", success=True)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or expired"
        )


@router.get("/profile/picture", response_class=Response, summary="Read your private profile picture")
def get_profile_picture(
    authorization: str = Header(""), auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> Response:
    user = auth_service.get_current_user(extract_bearer_token(authorization))
    content, mime = auth_service.read_profile_picture(user)
    return Response(content, media_type=mime, headers={
        "Cache-Control": "private, no-store", "Vary": "Authorization", "X-Content-Type-Options": "nosniff",
    })


@router.post(
    "/profile/picture",
    response_model=ProfilePictureResponse,
    summary="Upload profile picture",
    description="""
    Upload a new profile picture for the current user.
    
    **Supported formats:** JPEG, PNG, GIF, WebP
    **Maximum file size:** 5MB
    """
)
def upload_profile_picture(
    file: UploadFile = File(..., description="Profile picture image"),
    authorization: str = Header(..., description="Bearer access_token"),
    auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> ProfilePictureResponse:
    """Upload a profile picture for the current user."""
    token = extract_bearer_token(authorization)
    user = auth_service.get_current_user(token)
    
    # Validate file type
    allowed_types = AVATAR_EXTENSIONS
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed types: {', '.join(allowed_types)}"
        )
    
    # Validate file size (5MB max)
    max_size = 5 * 1024 * 1024
    content = file.file.read(max_size + 1)
    if len(content) > max_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds 5MB limit"
        )
    
    # Upload to Supabase Storage
    from core.file_validator import FileValidator
    FileValidator(max_size=max_size, allowed_mime_types=set(allowed_types)).validate(content, file.content_type)
    path = auth_service.upload_profile_picture(user.id, content, file.content_type)
    
    return ProfilePictureResponse(path=path, message="Profile picture uploaded successfully")


@router.post(
    "/change-password",
    response_model=MessageResponse,
    summary="Change password",
    description="""
    Change the user's password after verifying the current password.
    
    **Password Requirements:**
    - Minimum 8 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    """
)
def change_password(
    password_data: ChangePasswordRequest,
    authorization: str = Header(..., description="Bearer access_token"),
    auth_service: SupabaseAuthService = Depends(get_auth_service)
) -> MessageResponse:
    """Change user password with current password verification."""
    token = extract_bearer_token(authorization)
    user = auth_service.get_current_user(token)
    
    return auth_service.change_password(
        email=user.email,
        current_password=password_data.current_password,
        new_password=password_data.new_password
    )
