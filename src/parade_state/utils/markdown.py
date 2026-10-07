"""Safe markdown rendering for user-authored board content (issue 24).

Discussion posts and comments are markdown, displayed back to every
admin on the deployment. The rendered HTML must therefore never carry
executable content: raw HTML in the source is escaped (markdown2
``safe_mode="escape"``), and markdown-generated links are post-processed
to neutralise dangerous URL schemes, which safe_mode alone does not
scrub (``[x](javascript:...)``).

Render here and insert the result unescaped; every other user field
goes through the template ``| e`` filter. Keeping the sanitisation in
one testable function is the point of this module.
"""

import re

import markdown2  # pyright: ignore[reportMissingTypeStubs] — no stubs ship with markdown2

# markdown2 extras that stay inside the safe subset: code fences for
# bug reports. No extras that emit raw HTML/attributes.
_EXTRAS = ["fenced-code-blocks"]

# href/src attributes pointing at executable or data-URI schemes.
# Group 1 is the scheme prefix (case-insensitive, whitespace-tolerant),
# so the substitution keeps the attribute shape valid but inert.
_UNSAFE_SCHEME = re.compile(
    r"""(?i)(href|src)\s*=\s*(["'])\s*(javascript|vbscript|data)\s*:"""
)


def render_markdown(text: str) -> str:
    """Render ``text`` as a safe markdown subset.

    Raw HTML is escaped (shown as text, never executed) and links using
    the ``javascript:`` / ``vbscript:`` / ``data:`` schemes are rewritten
    to ``#``.
    """
    html = markdown2.markdown(text, safe_mode="escape", extras=_EXTRAS)
    return _UNSAFE_SCHEME.sub(r"\1=\2#", html)
