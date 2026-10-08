"""E2E: Add User and inline role changes on /admin/users in a real browser.

Regression coverage for the "added as admin, shows as super admin" report:
the creation path stores exactly the chosen role, and a role change whose
PATCH fails (HTTP or network) snaps the row's selects back to the stored
value instead of leaving the page showing a role the API never saved.
"""

import pytest

pytestmark = pytest.mark.e2e

NEW_USER_EMAIL = "newpromotion@example.com"


def _add_user(page, base_url: str, role_label: str) -> None:
    """Add a user through the real form; the page reloads on success."""
    page.goto(f"{base_url}/admin/users")
    page.fill("#newUserEmail", NEW_USER_EMAIL)
    page.fill("#newUserName", "Promotion Test")
    page.select_option("#newUserRole", label=role_label)
    page.click("button:has-text('Add')")
    # Wait for the reloaded table (the success toast also mentions the
    # email, so anchor on the row element itself).
    page.wait_for_selector(f"tr[data-user-id]:has-text('{NEW_USER_EMAIL}')")


def test_add_user_with_admin_role_shows_admin(page, e2e_server):
    _add_user(page, e2e_server, "Admin")

    selected = page.evaluate(
        """(email) => {
            const rows = [...document.querySelectorAll('tr[data-user-id]')];
            const row = rows.find(r => r.textContent.includes(email));
            if (!row) return null;
            const sel = row.querySelector('.role-select');
            return sel.options[sel.selectedIndex].text;
        }""",
        NEW_USER_EMAIL,
    )
    assert selected == "Admin"


def test_failed_role_patch_snaps_select_back(page, e2e_server):
    _add_user(page, e2e_server, "Admin")

    # Abort the PATCH at the network layer: the change must not stick in
    # the UI — the select reverts to the server-rendered role.
    page.route("**/api/v1/users/*", lambda route: route.abort())
    page.evaluate(
        """(email) => {
            const rows = [...document.querySelectorAll('tr[data-user-id]')];
            const row = rows.find(r => r.textContent.includes(email));
            const sel = row.querySelector('.role-select');
            sel.value = 'super_admin';
            sel.dispatchEvent(new Event('change'));
        }""",
        NEW_USER_EMAIL,
    )
    page.wait_for_function(
        """() => {
            const email = '"""
        + NEW_USER_EMAIL
        + """';
            const rows = [...document.querySelectorAll('tr[data-user-id]')];
            const row = rows.find(r => r.textContent.includes(email));
            return row && row.querySelector('.role-select').value === 'admin';
        }"""
    )
    assert "change not saved" in page.inner_text("#statusMessage")
    assert "Network error" in page.inner_text("#statusMessage")

    page.unroute("**/api/v1/users/*")


def test_successful_role_patch_keeps_select(page, e2e_server):
    _add_user(page, e2e_server, "Admin")

    page.evaluate(
        """(email) => {
            const rows = [...document.querySelectorAll('tr[data-user-id]')];
            const row = rows.find(r => r.textContent.includes(email));
            const sel = row.querySelector('.role-select');
            sel.value = 'super_admin';
            sel.dispatchEvent(new Event('change'));
        }""",
        NEW_USER_EMAIL,
    )
    page.wait_for_function(
        """() => document.querySelector('#statusMessage').textContent.includes('Updated role successfully')"""
    )

    # The stored role really changed: a fresh page load renders super admin.
    page.reload()
    page.wait_for_selector(f"tr[data-user-id]:has-text('{NEW_USER_EMAIL}')")
    selected = page.evaluate(
        """(email) => {
            const rows = [...document.querySelectorAll('tr[data-user-id]')];
            const row = rows.find(r => r.textContent.includes(email));
            const sel = row.querySelector('.role-select');
            return sel.options[sel.selectedIndex].text;
        }""",
        NEW_USER_EMAIL,
    )
    assert selected == "Super Admin"
