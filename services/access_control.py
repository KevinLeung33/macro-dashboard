"""Session-scoped read-only/admin access control for the Streamlit UI."""
import base64
import hashlib
import hmac
import os
import secrets
import time

import streamlit as st


ADMIN_SESSION_KEY = "dashboard_admin_authenticated_at"
ADMIN_PASSWORD_INPUT_KEY = "dashboard_admin_password_input"
DEFAULT_SESSION_MINUTES = 60


def _session_timeout_seconds():
    try:
        return max(5, int(os.getenv("DASHBOARD_ADMIN_SESSION_MINUTES", DEFAULT_SESSION_MINUTES))) * 60
    except (TypeError, ValueError):
        return DEFAULT_SESSION_MINUTES * 60


def _configured_password():
    return os.getenv("DASHBOARD_ADMIN_PASSWORD", "").strip()


def _configured_hash():
    return os.getenv("DASHBOARD_ADMIN_PASSWORD_HASH", "").strip()


def is_admin_authenticated():
    authenticated_at = st.session_state.get(ADMIN_SESSION_KEY)
    if not authenticated_at:
        return False
    try:
        valid = time.time() - float(authenticated_at) <= _session_timeout_seconds()
    except (TypeError, ValueError):
        valid = False
    if not valid:
        st.session_state.pop(ADMIN_SESSION_KEY, None)
    return valid


def _verify_password(password):
    configured_hash = _configured_hash()
    if configured_hash:
        try:
            algorithm, iterations, salt, expected = configured_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            salt_bytes = base64.urlsafe_b64decode(salt + "=" * (-len(salt) % 4))
            derived = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt_bytes,
                int(iterations),
            )
            return hmac.compare_digest(
                base64.urlsafe_b64encode(derived).decode("ascii").rstrip("="),
                expected,
            )
        except (ValueError, TypeError, UnicodeError):
            return False
    configured_password = _configured_password()
    return bool(configured_password) and hmac.compare_digest(password, configured_password)


def make_password_hash(password, iterations=310_000):
    """Create a portable PBKDF2 hash for DASHBOARD_ADMIN_PASSWORD_HASH."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"pbkdf2_sha256${iterations}${salt_text}${digest_text}"


def render_admin_access():
    """Render the shared sidebar unlock control and return current access state."""
    if is_admin_authenticated():
        st.sidebar.success("管理员操作已解锁")
        st.sidebar.caption(f"会话有效期约 {_session_timeout_seconds() // 60} 分钟")
        if st.sidebar.button("退出管理员模式", key="dashboard_admin_logout"):
            st.session_state.pop(ADMIN_SESSION_KEY, None)
            st.rerun()
        return True

    configured = bool(_configured_password() or _configured_hash())
    with st.sidebar.expander("管理员操作", expanded=False):
        if not configured:
            st.warning("尚未配置管理员密码，当前只能只读浏览。")
        else:
            st.caption("浏览无需密码；刷新、写入和配置修改需要解锁。")
            st.text_input("管理员密码", type="password", key=ADMIN_PASSWORD_INPUT_KEY)
            if st.button("解锁管理员操作", key="dashboard_admin_unlock", type="primary"):
                password = st.session_state.get(ADMIN_PASSWORD_INPUT_KEY, "")
                if _verify_password(password):
                    st.session_state[ADMIN_SESSION_KEY] = time.time()
                    st.session_state.pop(ADMIN_PASSWORD_INPUT_KEY, None)
                    st.rerun()
                else:
                    st.error("密码不正确")
    return False


def require_admin(action="此操作"):
    """Guard a mutating UI action; returns False for read-only visitors."""
    if is_admin_authenticated():
        return True
    st.warning(f"{action}需要先在侧边栏解锁管理员操作。")
    return False


if __name__ == "__main__":
    import getpass

    print(make_password_hash(getpass.getpass("Admin password: ")))
