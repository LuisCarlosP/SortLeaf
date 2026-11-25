"""
Authentication exceptions.
"""
from exceptions.base_exception import SortLeafException


class AuthenticationError(SortLeafException):
    """Raised when authentication fails."""
    
    def __init__(self, message: str = "Invalid credentials"):
        super().__init__(
            message=message,
            code=401,
            error_type="AUTH_ERROR"
        )


class UserAlreadyExistsError(SortLeafException):
    """Raised when trying to register with an existing email."""
    
    def __init__(self, email: str):
        super().__init__(
            message=f"User with email '{email}' already exists",
            code=409,
            error_type="USER_EXISTS"
        )


class UserNotFoundError(SortLeafException):
    """Raised when user is not found."""
    
    def __init__(self, identifier: str = ""):
        message = f"User not found: {identifier}" if identifier else "User not found"
        super().__init__(
            message=message,
            code=404,
            error_type="USER_NOT_FOUND"
        )


class InvalidTokenError(SortLeafException):
    """Raised when token is invalid or expired."""
    
    def __init__(self, message: str = "Invalid or expired token"):
        super().__init__(
            message=message,
            code=401,
            error_type="INVALID_TOKEN"
        )


class TokenRevokedError(SortLeafException):
    """Raised when token has been revoked."""
    
    def __init__(self):
        super().__init__(
            message="Token has been revoked",
            code=401,
            error_type="TOKEN_REVOKED"
        )


class InactiveUserError(SortLeafException):
    """Raised when user account is deactivated."""
    
    def __init__(self):
        super().__init__(
            message="User account is deactivated",
            code=403,
            error_type="USER_INACTIVE"
        )


class WeakPasswordError(SortLeafException):
    """Raised when password doesn't meet requirements."""
    
    def __init__(self, message: str = "Password does not meet security requirements"):
        super().__init__(
            message=message,
            code=400,
            error_type="WEAK_PASSWORD"
        )
