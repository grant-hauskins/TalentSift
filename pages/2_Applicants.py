"""Applicants: upload or import resumes, see parse status, and preview exactly what the AI will see."""

from pathlib import Path

import pandas as pd
import streamlit as st
from sqlmodel import select

from talentsift import ui
from talentsift.config import PROJECT_ROOT
from talentsift.ingest import (
    ADDED,
    already_ingested,
    default_batch_name,
    ingest_folder,
    ingest_upload,
    list_batches,
    remask_applicants,
    scan_folder,
)
from talentsift.models import SCREENABLE_PARSE_STATUSES, Applicant, loads

SAMPLES_DIR = PROJECT_ROOT / "data" / "resumes" / "samples"
ALL = "All batches"
BROWSE_KEY = "browse_dir"

settings = ui.page_setup(
    "Applicants",
    "PDF resumes only. Names, contact details, addresses, links, and images are masked before any AI sees the resume.",
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


def fmt_size(size: int) -> str:
    return f"{size / 1_048_576:.1f} MB" if size >= 1_048_576 else f"{max(size, 1) / 1024:.0f} KB"


def go_to(path: Path) -> None:
    """Navigate the folder browser. Also syncs the path box, which can only be set before it is drawn."""
    st.session_state[BROWSE_KEY] = str(path)
    st.session_state["folder_path_box"] = str(path)


def subfolders(folder: Path) -> list[Path]:
    try:
        return sorted((p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name.lower())
    except OSError:
        return []


def folder_browser() -> Path | None:
    """Pick a folder on this computer: type or paste a path, or click through folders."""
    if BROWSE_KEY not in st.session_state:
        go_to(SAMPLES_DIR)
    current = Path(st.session_state[BROWSE_KEY]).expanduser()

    def typed_path() -> None:
        typed = Path(st.session_state["folder_path_box"].strip().strip('"')).expanduser()
        st.session_state[BROWSE_KEY] = str(typed)

    st.text_input(
        "Folder",
        key="folder_path_box",
        on_change=typed_path,
        help="Paste a folder path, or browse with the buttons below. The folder is read on the computer running TalentSift.",
    )
    nav = st.columns([1, 1, 1, 3])
    nav[0].button("⬆️ Up", on_click=go_to, args=(current.parent,), disabled=current.parent == current, width="stretch")
    nav[1].button("🏠 Home", on_click=go_to, args=(Path.home(),), width="stretch")
    nav[2].button("🧪 Samples", on_click=go_to, args=(SAMPLES_DIR,), width="stretch")

    if not current.is_dir():
        st.error(f"Folder not found: {current}")
        return None
    children = subfolders(current)
    if children:

        def open_child() -> None:
            chosen = st.session_state.get("open_subfolder")
            st.session_state["open_subfolder"] = None
            if chosen:
                go_to(current / chosen)

        nav[3].selectbox(
            "Open a subfolder",
            [c.name for c in children],
            index=None,
            placeholder=f"Open a subfolder ({len(children)})",
            key="open_subfolder",
            on_change=open_child,
            label_visibility="collapsed",
        )
    return current


def folder_import(session) -> None:
    folder = folder_browser()
    if folder is None:
        return
    try:
        scan = scan_folder(folder)
    except OSError as exc:
        st.error(f"Could not read {folder}: {exc}")
        return

    included, excluded = scan.included, scan.excluded
    duplicates = already_ingested(session, [f.path for f in included])
    new = [f for f in included if f.path not in duplicates]

    cols = st.columns(3)
    cols[0].metric("PDFs to send", len(new), help="New PDF resumes that will be parsed, masked, and screened.")
    cols[1].metric("Already imported", len(duplicates), help="Identical files already in TalentSift; they are skipped.")
    cols[2].metric("Ignored (not PDF)", len(excluded), help="Only PDF files are ever read from the folder.")
    st.caption(
        "**What the AI receives:** only the text extracted from each PDF, after names, contact details, addresses, "
        "links, and images are masked. The files themselves never leave this computer. Subfolders are not included."
    )

    if included:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "file": f.name,
                        "size": fmt_size(f.size_bytes),
                        "will be": f"skipped: already {duplicates[f.path]}" if f.path in duplicates else "imported and screened",
                    }
                    for f in included
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("No PDF files in this folder.")
    if excluded:
        with st.expander(f"Files that will not be sent ({len(excluded)})"):
            st.dataframe(
                pd.DataFrame([{"file": f.name, "reason": f.reason} for f in excluded]),
                hide_index=True,
                width="stretch",
            )

    batch = st.text_input("Batch name", placeholder=f"Defaults to the folder name: {folder.name}", key="folder_batch")
    if st.button(
        f"Import {len(new)} PDF{'s' if len(new) != 1 else ''}",
        type="primary",
        disabled=not new,
        key="import_folder",
    ):
        with st.spinner(f"Parsing and masking {len(new)} PDF(s)..."):
            st.session_state.folder_results = ingest_folder(session, folder, settings=settings, batch=batch.strip() or None)
        st.rerun()  # refresh the preview so the new files show as already imported
    if results := st.session_state.pop("folder_results", None):
        show_results(results)


with ui.db_session() as session:
    upload_tab, folder_tab = st.tabs(["Upload files", "Import a folder"])
    with upload_tab:
        with st.form("upload", clear_on_submit=True):
            files = st.file_uploader(
                "Resumes (PDF only)", type=["pdf"], accept_multiple_files=True
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
        folder_import(session)

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
