"""Settings: store the OpenRouter API key and model from inside the app."""

import streamlit as st

from talentsift import credentials, ui
from talentsift.credentials import CredentialError, clear_api_key, key_warning, mask_key, save_api_settings
from talentsift.llm import describe_provider

settings = ui.page_setup(
    "Settings",
    "Connect a real model. The key is saved to the local .env file on this computer: never to the database, "
    "the audit log, or git.",
    icon="🔑",
)

if flash := st.session_state.pop("settings_flash", None):
    st.success(flash)

st.subheader("OpenRouter API key")
left, right = st.columns(2)
left.metric("Stored key", mask_key(settings.openrouter_api_key))
right.metric("Model", settings.llm_model or "(not set)")
st.caption(describe_provider(settings, "openrouter"))

with st.form("api_settings", clear_on_submit=True):
    api_key = st.text_input(
        "API key",
        type="password",
        placeholder="sk-or-..." if not settings.openrouter_api_key else "Leave blank to keep the stored key",
        help="Create a key at openrouter.ai/keys and set a spending limit on it.",
    )
    model = st.text_input(
        "Model slug",
        value=settings.llm_model or "openai/gpt-4o-mini",
        help="Any OpenRouter model slug. Models with structured-output support work best.",
    )
    saved = st.form_submit_button("Save and use OpenRouter", type="primary")

if saved:
    try:
        save_api_settings(api_key, model)
    except CredentialError as exc:
        st.error(str(exc))
    else:
        ui.switch_provider("openrouter")
        message = "Saved. Screening and rubric drafting now use OpenRouter."
        if warning := key_warning(api_key):
            message += f" Note: {warning}"
        st.session_state.settings_flash = message
        st.rerun()

if settings.openrouter_api_key and st.button("Remove stored key", help="Deletes the key from .env and switches to the offline client."):
    clear_api_key()
    ui.switch_provider("fake")
    st.session_state.settings_flash = "Key removed. The app is using the offline fake client."
    st.rerun()

with st.expander("Where is the key stored?"):
    st.markdown(
        f"""
- File: `{credentials.ENV_PATH}` (listed in `.gitignore`, so it is never committed).
- The key is shown masked here and is never written to the database or the audit log.
- Each screening run still records which model and provider it used.
- **Hosted on Streamlit Community Cloud?** That disk resets on reboot. Put `OPENROUTER_API_KEY` and
  `LLM_MODEL` in the app's **Settings > Secrets** instead.
"""
    )
