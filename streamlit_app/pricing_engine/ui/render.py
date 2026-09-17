"""Render pricing HTML without markdown code-block artifacts."""
from __future__ import annotations

import streamlit as st


def compact_html(html: str) -> str:
    """Remove leading whitespace so st.markdown never treats rows as code blocks."""
    return "".join(line.strip() for line in html.splitlines())


def render_html(html: str) -> None:
    """Render raw HTML without markdown code-block parsing."""
    # st.html uses an iframe and drops app CSS — keep markdown with compact HTML.
    st.markdown(compact_html(html), unsafe_allow_html=True)
