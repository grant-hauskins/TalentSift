"""Applicants: upload or import resumes, see parse status, and preview exactly what the AI will see."""

import pandas as pd
import streamlit as st
from sqlmodel import select

from talentsift import ui
from talentsift.config import PROJECT_ROOT
from talentsift.ingest import (
    ADDED,
    default_batch_name,
    ingest_folder,
    ingest_upload,
    list_batches,
    remask_applicants,
)
from talentsift.models import SCREENABLE_PARSE_STATUSES, Applicant, loads
from talentsift.parsers import supported_extensions

SAMPLES_DIR = PROJECT_ROOT / "data" / "resumes" / "samples"
ALL = "All batches"

settings = ui.page_setup(
    "Applicants",
    "Text-based PDFs only for now. Names, contact details, addresses, links, and images are masked before any AI sees the resume.",
    icon="📄",
)


def show_results(results) -> None:
    added = [r for r in results if r.outcome == ADDED]
    st.success(f"{len(added)} added, {len(results) - len(added)} skipped as duplicates.")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "file": r.filename,
                    "result": "added" if r.outcome == ADDED else "duplicate (skipped)",
                    "applicant": r.applicant.display_label if r.applicant else "",
                    "parse status": f"{ui.PARSE_ICONS.get(r.parse_status, '')} {r.parse_status}",
                    "note": r.message,
                }
                for r in results
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    for r in results:
        if r.outcome == ADDED and not r.screenable:
            st.warning(f"**{r.filename}**: {r.message}")


with ui.db_session() as session:
    upload_tab, folder_tab = st.tabs(["Upload files", "Import a folder"])
    with upload_tab:
        with st.form("upload", clear_on_submit=True):
            files = st.file_uploader(
                "Resumes", type=[ext.lstrip(".") for ext in supported_extensions()], accept_multiple_files=True
            )
            batch = st.text_input("Batch name", value=default_batch_name())
            submitted = st.form_submit_button("Ingest files", type="primary")
        if submitted and files:
            with st.spinner(f"Parsing and masking {len(files)} file(s)..."):
                results = [
                    ingest_upload(session, f.name, f.getvalue(), batch=batch.strip() or default_batch_name(), settings=settings)
                    for f in files
                ]
            show_results(results)
    with folder_tab:
        with st.form("folder"):
            folder = st.text_input("Folder path", value=str(SAMPLES_DIR))
            folder_batch = st.text_input("Batch name (defaults to the folder name)")
            imported = st.form_submit_button("Import every PDF in this folder", type="primary")
        if imported:
            try:
                with st.spinner("Importing..."):
                    results = ingest_folder(session, folder, settings=settings, batch=folder_batch.strip() or None)
                if results:
                    show_results(results)
                else:
                    st.info("No supported files found in that folder.")
            except FileNotFoundError as exc:
                st.error(str(exc))

    st.divider()
    batches = list_batches(session)
    if not batches:
        st.info("No applicants yet. Upload resumes or import the sample folder above.")
        st.stop()

    batch_filter = st.selectbox("Batch", [ALL] + batches)
    query = select(Applicant).order_by(Applicant.id)
    if batch_filter != ALL:
        query = query.where(Applicant.batch == batch_filter)
    applicants = list(session.exec(query))
    screenable = sum(a.parse_status in SCREENABLE_PARSE_STATUSES for a in applicants)
    st.caption(f"{len(applicants)} applicant(s), {screenable} ready to screen.")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "applicant": a.display_label,
                    "batch": a.batch,
                    "original file": a.original_filename,
                    "parse status": f"{ui.PARSE_ICONS.get(a.parse_status, '')} {a.parse_status}",
                    "pages": a.page_count,
                    "characters": len(a.raw_text),
                    "items masked": sum(loads(a.mask_counts_json, default={}).values()),
                    "note": a.parse_message,
                }
                for a in applicants
            ]
        ),
        hide_index=True,
        width="stretch",
    )

    st.subheader("Preview")
    chosen = st.selectbox(
        "Applicant",
        applicants,
        format_func=lambda a: f"{a.display_label} · {a.original_filename}",
    )
    if chosen is not None:
        show_masked = st.toggle("Show masked text (exactly what the AI sees)", value=True)
        counts = loads(chosen.mask_counts_json, default={})
        if counts:
            masked_items = ", ".join(f"{name.replace('_', ' ')} {count}" for name, count in counts.items() if count)
            st.caption(f"Masked: {masked_items or 'nothing'}")
        if chosen.parse_message:
            st.warning(chosen.parse_message)
        text = chosen.masked_text if show_masked else chosen.raw_text
        st.text_area("Resume text", text or "(no text extracted)", height=420, disabled=True, label_visibility="collapsed")

    with st.expander("Masking settings"):
        st.write(
            f"Graduation-year masking is **{'on' if settings.mask_grad_years else 'off'}** "
            "(set `MASK_GRAD_YEARS` in .env). After changing it, re-apply masking to existing applicants."
        )
        if st.button("Re-apply masking with current settings"):
            changed = remask_applicants(session, settings)
            st.success(f"Re-masked {changed} applicant(s). Changed resumes will be re-scored on the next run.")
