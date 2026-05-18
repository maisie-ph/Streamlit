import streamlit as st

from utils.data_loader import (
    load_community_data,
    find_communities_for_bic,
    is_community_too_large,
    load_bic_neighborhood,
    MAX_ENTITIES_FULL_GRAPH,
    RISK_TYPE_OPTIONS,
)
from components.graph_generator import generate_community_graph
from utils.format_amount import format_amount
from components.data_display import display_full_community_data

import os
import pandas as pd
import gc
import json
import tempfile
import subprocess
import sys
from pathlib import Path


# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Search by BIC code",
    page_icon="🏦",
    layout="wide"
)

# ── Loading styles ─────────────────────────────────────────────────────────────
with open("styles.css") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
st.markdown(
    """
    <style>
    .block-container {
    max-width: 1500px;
    padding-left: 2rem;
    padding-right: 2rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Loading config file ────────────────────────────────────────────────────────
with open("config.json", "r") as f:
    config = json.load(f)

# ── Configuration ──────────────────────────────────────────────────────────────
DATA_PATH = config["DATA_PATH"]
MAX_EDGES_DISPLAY = config["MAX_EDGES_DISPLAY"]
FILE_NAME = config["FILE_NAME"]
PATH_RFI_MAIN = Path(__file__).parent.parent.parent / "Refact_RFI_Template" / "main.py"

# ── Graph legend ──────────────────────────────────────────────────────────────
def chip(color, label):
    return (
        f'<span style="display:inline-flex;align-items:center;gap:8px;'
        f'margin-right:20px;font-size:14px;">'
        f'<span style="width:14px;height:14px;border-radius:50%;'
        f'background:{color};border:1px solid #333;display:inline-block;"></span>'
        f'{label}</span>'
    )

legend_html = (
    chip("#0018F9", "Sayari critical risk")
    + chip("#FF0000", "FSI")
    + chip("#086776", "Politically exposed person (PEP)")
    + chip("#FF5F15", "WI entity")
    + chip("#3c1414", "SSD/SSZ Bank")
    + chip("#DBDBDB", "No risk account")
    + chip("#B90DDC", "BIC-linked account")
)

# ── Title and description ──────────────────────────────────────────────────────
st.title("🏦 Search for High Risk Banks by BIC code")
st.markdown("""
    Search for bank by **BIC code** to find all communities it appears in.
    Select one or multiple communities to display the transaction network. 
""")

# ── Session state initialization ───────────────────────────────────────────────
for key, default in {
    "bic_candidates": None,       # list of dicts from find_communities_for_bic
    "bic_searched": None,         # the BIC that was searched
    "bic_graph_loaded": False,
    "bic_df": None,
    "bic_community_component": None,
    "bic_html_content": None,
    "bic_graph_title": None,
    "bic_value": None,
    "_bic_prev_risk_filter": [],  # tracks filter changes to reset search results
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


def reset_bic_search():
    """Clear BIC search results and graph state."""
    for k in ["bic_candidates", "bic_searched", "bic_df", "bic_html_content",
              "bic_graph_title", "bic_value", "bic_community_component"]:
        st.session_state[k] = None
    st.session_state["bic_graph_loaded"] = False


# ── Step 1 — Risk typologies filter ───────────────────────────────────────────

st.subheader("1. Select risk typologies")
st.markdown(
    "Pick one or more risk types to restrict communities to those that carry those risks. "
    "Leave empty to search across all communities."
)

selected_risk_typologies = st.multiselect(
    "Risk typologies",
    options=RISK_TYPE_OPTIONS,
    default=[],
    placeholder="All communities (no filter)",
    key="bic_risk_typologies_filter",
    label_visibility="collapsed",
)

# When the filter changes, wipe current search results so stale selections can't carry over
if selected_risk_typologies != st.session_state["_bic_prev_risk_filter"]:
    st.session_state["_bic_prev_risk_filter"] = selected_risk_typologies
    reset_bic_search()
    st.rerun()

st.divider()

# ── Step 2: BIC search ─────────────────────────────────────────────────────────

st.subheader("2. Search by BIC code")

bic_input = st.text_input(
    "BIC Code",
    value="",
    help="Enter exactly 6 characters BIC code to find all communities it appears in",
    placeholder="e.g. BNPAFR",
    max_chars=6
)

# Reset when the BIC input no longer matches the loaded graph (same mechanism as page 1)
if st.session_state.bic_graph_loaded and bic_input.strip().upper() != (st.session_state.bic_value or ""):
    reset_bic_search()
    st.rerun()

# Show helper message while typing
if bic_input and len(bic_input.strip()) < 6:
    st.caption(f"Please enter 6 letter BIC code ({len(bic_input.strip())}/6)")

if st.button("🔍 Search BIC", type="secondary", disabled=len(bic_input.strip()) != 6):
    if not bic_input.strip():
        st.error("Please enter a BIC code")
    elif len(bic_input.strip()) != 6:
        st.error("Please enter 6 letter BIC code")
    elif not os.path.exists(os.path.join(DATA_PATH, FILE_NAME)):
        st.error("Data file not found")
    else:
        with st.spinner(f"Searching communities for BIC {bic_input.strip().upper()}..."):
            try:
                candidates = find_communities_for_bic(DATA_PATH, bic_input.strip())
                st.session_state.bic_candidates = candidates
                st.session_state.bic_searched = bic_input.strip().upper()
                # Reset graph when doing a new search
                st.session_state.bic_graph_loaded = False
                st.session_state.bic_df = None
            except Exception as e:
                st.error(f"❌ {str(e)}")
                st.session_state.bic_candidates = None


# ── Step 3: Community selection table ─────────────────────────────────────────
if st.session_state.bic_candidates:
    candidates = st.session_state.bic_candidates
    bic_searched = st.session_state.bic_searched

    st.subheader("3. Select Communities")

    if selected_risk_typologies:
        candidates = [
            c for c in candidates
            if any(r in c.get('risk_types', '').split(',') for r in selected_risk_typologies)
        ]
        if not candidates:
            st.warning(
                f"No communities found for BIC `{bic_searched}` with risk type(s): "
                f"{', '.join(selected_risk_typologies)}"
            )
        else:
            st.markdown(
                f"**{len(candidates)}** community(ies) found for BIC `{bic_searched}` "
                f"matching **{', '.join(selected_risk_typologies)}** — select one or more to display:"
            )
    else:
        st.markdown(f"**{len(candidates)}** community(ies) found for BIC `{bic_searched}` — select one or more to display:")

    candidates_df = pd.DataFrame(candidates).rename(columns={"risk_types": "Risk Types in Community"})

    selection = st.dataframe(
        candidates_df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row"   # ← allows selecting multiple communities
    )

    selected_rows = selection.selection.rows

    if selected_rows:
        selected_communities = [candidates[i]['Community ID'] for i in selected_rows]
        st.success(f"Selected {len(selected_communities)} community(ies): {selected_communities}")

        # ── Step 4: Generate graph button ─────────────────────────────────────
        st.subheader("4. Generate Network")
        if st.button("🔍 Generate Network for Selected Communities", type="primary"):
            gc.collect()

            with st.spinner("Loading transactions..."):
                try:
                    # Check size per community
                    too_large_communities = []
                    normal_communities = []
                    for c in selected_communities:
                        tl, ec = is_community_too_large(DATA_PATH, c)
                        if tl:
                            too_large_communities.append(c)
                        else:
                            normal_communities.append(c)

                    dfs = []

                    # Normal communities — load fully
                    for c in normal_communities:
                        dfs.append(load_community_data(community_component=c, path_data=DATA_PATH))

                    # Large communities — load only BIC neighborhood
                    for c in too_large_communities:
                        st.info(
                            f"⚠️ Community **{c}** is too large — "
                            f"showing only BIC `{bic_searched}` accounts and their direct neighbors."
                        )
                        dfs.append(load_bic_neighborhood(
                            path_data=DATA_PATH,
                            bic=bic_searched,
                            community_component=c
                        ))

                    df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

                    graph_title = f"BIC {bic_searched} — Communities {selected_communities}"

                    if df.empty:
                        st.warning(f"No transactions found for {graph_title}")
                        st.stop()

                    # Generate graph
                    with st.spinner("Generating graph visualization..."):
                        graph_file = generate_community_graph(
                            df,
                            max_edges=MAX_EDGES_DISPLAY,
                            highlight_account_id=None,
                            highlight_account_name=None,
                            highlight_bic=bic_searched
                        )

                    html_content = None
                    if graph_file:
                        with open(graph_file, 'r', encoding='utf-8') as f:
                            html_content = f.read()

                    # Save to session state
                    st.session_state.bic_df = df
                    st.session_state.bic_community_component = selected_communities
                    st.session_state.bic_html_content = html_content
                    st.session_state.bic_graph_title = graph_title
                    st.session_state.bic_value = bic_searched
                    st.session_state.bic_graph_loaded = True

                except Exception as e:
                    st.error(f"❌ An error occurred: {str(e)}")
                    st.exception(e)
                finally:
                    gc.collect()
    else:
        st.caption("Click one or more rows to select communities")


# ── Display section ────────────────────────────────────────────────────────────
if st.session_state.bic_graph_loaded and st.session_state.bic_df is not None:

    df = st.session_state.bic_df
    community_component = st.session_state.bic_community_component
    html_content = st.session_state.bic_html_content
    graph_title = st.session_state.bic_graph_title
    bic_value = st.session_state.bic_value

    st.success(f"✅ Loaded {len(df):,} transactions — {graph_title}")

    col_info, col_graph = st.columns([0.3, 0.7])

    with col_info:
        unique_accounts = (
            set(df['ORIGINATOR_ACC_NUMBER'].dropna()) |
            set(df['BENEFICIARY_ACC_NUMBER'].dropna())
        )
        unique = len(unique_accounts)
        total_amount = df['BASE_CURR_AMOUNT'].sum()
        formatted_amount = format_amount(total_amount)

        st.subheader("🏦 Information")

        date_min = df['EXECUTION_GLOBAL_DATE_TIME'].min().date()
        date_max = df['EXECUTION_GLOBAL_DATE_TIME'].max().date()

        community_id_display = ', '.join(str(c) for c in community_component)

        sender_count = (df['BIC_ORIGINATOR'].str.upper() == bic_value).sum()
        receiver_count = (df['BIC_BENEFICIARY'].str.upper() == bic_value).sum()

        info_data = {
            "Metric": [
                "BIC Code",
                "Community ID(s)",
                "Unique Accounts",
                "Total Transactions",
                "Transactions as Sender BIC",
                "Transactions as Receiver BIC",
                "Total Amount (EUR)",
                "Date Range"
            ],
            "Value": [
                bic_value,
                community_id_display,
                f"{unique}",
                f"{len(df):,}",
                f"{sender_count:,}",
                f"{receiver_count:,}",
                f"{formatted_amount}",
                f"{date_min} to {date_max}"
            ]
        }

        community_info = pd.DataFrame(info_data)
        st.markdown(
            "<style>div[data-testid='stTable'] table {font-size: 12px;}</style>",
            unsafe_allow_html=True,
        )
        st.table(community_info)

    with col_graph:
        with st.expander("Graph legend", expanded=False):
            st.markdown(legend_html, unsafe_allow_html=True)

        if html_content:
            st.components.v1.html(html_content, height=600, scrolling=True)

            st.download_button(
                label="📥 Download Full Graph",
                data=html_content,
                file_name=f"BIC_{bic_value}_communities_{community_component}_graph.html",
                mime="text/html"
            )

    display_full_community_data(
        df,
        community_component[0] if isinstance(community_component, list) else community_component
    )

    # ── Generate Report section ────────────────────────────────────────────────
    st.subheader("Generate Report")
    if st.button("📄 Generate and Download report"):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with st.spinner("Generating report, please wait..."):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(PATH_RFI_MAIN),
                        "--community", str(community_component[0] if isinstance(community_component, list) else community_component),
                        "--output_dir", tmp_dir
                    ],
                    capture_output=True,
                    text=True
                )

            # Debug — remove once working
            st.write("Return code:", result.returncode)
            st.code(result.stdout)
            st.code(result.stderr)

            if result.returncode != 0:
                st.error("Generation failed.")
            else:
                generated_files = list(Path(tmp_dir).iterdir())
                if not generated_files:
                    st.error("No file was generated.")
                else:
                    file_path = generated_files[0]
                    with open(file_path, "rb") as f:
                        file_bytes = f.read()

                    mime = (
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        if file_path.suffix == ".docx"
                        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

                    st.success("Report generated!")
                    st.download_button(
                        label="⬇️ Download report",
                        data=file_bytes,
                        file_name=file_path.name,
                        mime=mime
                    )

    del df
    gc.collect()


