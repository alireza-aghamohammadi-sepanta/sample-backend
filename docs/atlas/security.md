---
type: concept
title: Security and Cryptography
summary: Password hashing with Argon2, JWT issuance and verification, password reset tokens, and FastAPI current_user authentication.
related: ["auth.md", "config.md", "todos.md", "assets.md"]
source_paths: ["app/core/security.py"]
---

# Security and Cryptography

The application implements centralized cryptographic utilities and authentication helpers in `app/core/security.py`. This includes password hashing, JSON Web Token (JWT) handling, secure password reset token generation, and the `current_user` FastAPI dependency.

## Password Hashing

Passwords are encrypted using Argon2 via Passlib's `CryptContext`:

```python
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
```

- **`get_password_hash(password: str) -> str`**: Generates a salted Argon2 hash for storing in the `users` table.
- **`verify_password(plain_password: str, hashed_password: str) -> bool`**: Verifies whether a supplied plaintext password matches the stored hash in constant time.

Argon2 provides memory-hard hashing resistant to GPU-based cracking attacks.

## Access Tokens (JWT)

Authentication relies on signed bearer JWTs using the `HS256` symmetric signing algorithm and the `jwt_secret` configured in [configuration](config.md).

### Token Creation
- **`create_access_token(subject, expires_delta: timedelta | None = None) -> str`**:
  - Encodes a JSON payload containing:
    - `sub`: String representation of the subject (the user's UUID).
    - `iat`: Timestamp when the token was issued (in UTC).
    - `exp`: UTC expiration timestamp.
  - The default expiration is 60 minutes, configured via `ACCESS_TOKEN_EXPIRE_MINUTES` or overridden at runtime.

### Token Verification
- **`decode_access_token(token: str) -> dict`**:
  - Verifies the signature using `get_settings().jwt_secret`.
  - Validates `exp` timestamps.
  - Raises PyJWT exceptions (`ExpiredSignatureError`, `InvalidTokenError`) on invalid tokens.

## Password Reset Tokens

Password reset functionality uses cryptographically secure single-use tokens:
- **`generate_password_reset_token() -> str`**: Generates a 32-byte URL-safe string using Python's `secrets.token_urlsafe(32)`.
- **`hash_reset_token(token: str) -> str`**: Computes the SHA-256 digest of the token before saving it to the database (`app/models/password_reset_token.py`). This ensures that database leaks do not expose active plaintext reset tokens.

## Request Authentication: `current_user`

The `current_user` dependency is the cornerstone of endpoint authorization across [todos](todos.md) and [assets](assets.md):

```python
bearer_scheme = HTTPBearer(auto_error=False)

def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> uuid.UUID:
    ...
```

### Security Considerations of `current_user`
1. **Constant Error Response**: Any failure mode—missing Authorization header, invalid JWT signature, expired token, or non-UUID subject claim—raises an identical HTTP 401 response:
   ```json
   {
       "detail": "Could not validate credentials"
   }
   ```
   with header `WWW-Authenticate: Bearer`. This prevents information leakage regarding whether a token was malformed, expired, or absent.
2. **Strict UUID Validation**: The subject claim (`sub`) must parse cleanly into a standard `uuid.UUID`. This protects downstream subsystems (such as object key generation in Cloud Storage) from injection or invalid path strings.
