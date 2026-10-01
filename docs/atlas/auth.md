---
type: concept
title: User Accounts and Authentication
summary: User registration, login, password recovery flows, and account data models.
related: ["security.md", "database.md", "email.md"]
source_paths:
  - "app/api/auth.py"
  - "app/models/user.py"
  - "app/models/password_reset_token.py"
  - "app/schemas/user.py"
---

# User Accounts and Authentication

The authentication subsystem handles account creation, credential verification, and password reset flows. It is implemented across `app/api/auth.py`, `app/models/user.py`, `app/models/password_reset_token.py`, and `app/schemas/user.py`.

## Data Models

### User (`app/models/user.py`)
Represents an authenticated user account in the `users` table:
- `id`: UUID primary key, generated via `uuid.uuid4`.
- `email`: `String(320)`, unique, indexed. Normalized to lowercase without whitespace.
- `hashed_password`: `String(255)`, stored Argon2 hash.
- `created_at`: UTC timestamp of account creation.

### PasswordResetToken (`app/models/password_reset_token.py`)
Tracks single-use password reset tokens in the `password_reset_tokens` table:
- `id`: UUID primary key.
- `user_id`: UUID foreign key to `users.id` with cascade deletion.
- `token_hash`: `String(255)`, indexed SHA-256 hash of the generated raw token.
- `expires_at`: UTC timestamp (15 minutes after issuance).
- `created_at`: UTC timestamp of token issuance.
- `used_at`: UTC timestamp indicating when the token was consumed (or `None`).

## Request & Response Schemas (`app/schemas/user.py`)

- **`UserCreate`**: Registration request containing `email` and `password` (8 to 128 characters).
- **`UserLogin`**: Login request containing `email` and `password`.
- **`Token`**: Response payload returning `{"access_token": "...", "token_type": "bearer"}`.
- **`ForgotPasswordRequest`**: Request payload containing `email`.
- **`ResetPasswordRequest`**: Reset request containing `token` and `new_password` (supports alias `password`).
- **`UserRead`**: Public user profile (`id`, `email`).

Both `UserCreate`, `UserLogin`, and `ForgotPasswordRequest` provide a `normalized_email` property that strips surrounding whitespace and converts the address to lowercase.

## Authentication Endpoints

### 1. Registration (`POST /signup`)
- Checks if the normalized email is already registered. If found, returns HTTP 409 Conflict (`Email already registered`).
- Hashes password using Argon2 via [security](security.md).
- Persists new `User` record to PostgreSQL via [database](database.md).
- Safely catches database unique constraint race conditions (`IntegrityError`) and translates them to HTTP 409 without leaking internal database error messages.
- Returns a signed bearer token (`Token`).

### 2. Login (`POST /auth/login` and `POST /login`)
- Verifies credentials against the `users` table using Argon2 constant-time verification.
- Returns HTTP 401 Unauthorized (`Invalid credentials`) on missing user or incorrect password.
- Issues a signed JWT access token encoding the user's UUID in the `sub` claim.

### 3. Forgot Password (`POST /auth/forgot-password` and `POST /forgot-password`)
- **Anti-Enumeration Guarantee**: Always returns HTTP 200 with the generic response:
  ```json
  {"message": "If the email is registered, a password reset link has been sent."}
  ```
  regardless of whether the email exists in the database.
- If the email matches a registered user:
  1. Generates a secure 32-byte raw token via `secrets.token_urlsafe(32)`.
  2. Computes the SHA-256 hash of the raw token.
  3. Records a `PasswordResetToken` with an expiration window of 15 minutes (`RESET_TOKEN_EXPIRE_MINUTES = 15`).
  4. Dispatches a reset notification with the raw token using the [email service](email.md).

```
   Client                    Auth API                   EmailService
     |                           |                           |
     | POST /auth/forgot-password|                           |
     |-------------------------->|                           |
     |                           | Check if email exists     |
     |                           |------------------+        |
     |                           |                  |        |
     |                           |<-----------------+        |
     |                           | If exists:                |
     |                           | 1. Generate random token  |
     |                           | 2. Store SHA-256 hash     |
     |                           | 3. Send reset email       |
     |                           |-------------------------->|
     | HTTP 200 (Generic message)|                           |
     |<--------------------------|                           |
```

### 4. Reset Password (`POST /auth/reset-password` and `POST /reset-password`)
- Verifies that the supplied raw token's SHA-256 hash exists in `password_reset_tokens`.
- Validates that:
  - The token is unexpired (`expires_at > now`).
  - The token has not been used previously (`used_at is None`).
  - The referenced user account exists.
- Updates the user's password hash with Argon2.
- Marks the reset token as consumed (`used_at = now`).
- Returns a newly minted access token for the authenticated user.
