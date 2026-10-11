"""AUTH — passkey gate redirects, token single-use, session integrity."""

from checks.auth_checks import (
    login_page_renders,
    register_requires_token,
    session_integrity,
    token_single_use,
    unauthed_redirects,
)


def test_auth_unauthed_redirects(ops):
    unauthed_redirects(ops)


def test_auth_login_page_renders(ops):
    login_page_renders(ops)


def test_auth_register_requires_token(ops):
    register_requires_token(ops)


def test_auth_token_single_use(ops):
    token_single_use(ops)


def test_auth_session_integrity(ops):
    session_integrity(ops)
