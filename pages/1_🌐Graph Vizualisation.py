import streamlit as st

import os
import pandas as pd
import gc
import json

from utils.data_loader import (
    load_community_data,
    is_community_too_large,
    MAX_ENTITIES_FULL_GRAPH,
    RISK_TYPE_OPTIONS,
)
from utils.name_index_db import search_names, get_db_connection
from utils.account_index_db import search_accounts, get_account_db_connection
from utils.subgraph_data_loader import load_subgraph_data
from components.graph_generator import generate_community_graph
from utils.format_amount import format_amount
from utils.monthly_steps import _build_monthly_steps
from components.data_display import display_full_community_data

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Community Network",
    page_icon="📊",
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
MIN_SEARCH_CHARS = 3

# ── Connection with the db in cache resource ──────────────────────────────────
get_db_connection()
get_account_db_connection()


@st.cache_data(show_spinner=False)
def get_community_risk_types(community_id: str) -> str:
    """Return the comma-separated risk_types string for a community (from name index)."""
    try:
        con = get_db_connection()
        rows = con.execute(
            "SELECT DISTINCT risk_types FROM name_index WHERE community_index = ?",
            [int(float(community_id))]
        ).fetchall()
        return rows[0][0] or '' if rows else ''
    except Exception:
        return ''

# ── Session state defaults ─────────────────────────────────────────────────────

_DEFAULTS = {
    "df": None,
    "html_content": None,
    "data_loaded": False,
    "use_subgraph": False,
    "resolved_account_id": None,
    "community_component": None,
    "n_hops": 1,
    "monthly_steps": None,
    "selected_date": None,
    # Persisted input snapshot — what was actually used for the last load
    "loaded_inputs": {},
    # Tracks last input type to detect tab switches
    "last_input_type": None,
    # Fallback flow for oversized communities reached via Community ID
    "awaiting_fallback": False,
    "entity_count": None,
    "too_large": False,
    # Persisted Entity Name for display section (survives reruns)
    "resolved_account_name": None,
}

for key, val in _DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val

# Risk typologies filter lives outside _DEFAULTS so reset_all() doesn't clear it
if "_prev_risk_filter" not in st.session_state:
    st.session_state["_prev_risk_filter"] = []

# Helper functions for session states
def reset_all():
    """Full reset — call when input type changes or a fresh search is triggered."""
    for key, val in _DEFAULTS.items():
        st.session_state[key] = val

def reset_results_only():
    """Keep community/account resolution but clear loaded data and graph."""
    for key in ["df", "html_content", "data_loaded", "monthly_steps", "selected_date", "loaded_inputs", "awaiting_fallback"]:
        st.session_state[key] = _DEFAULTS[key]


# ── Title and description ─────────────────────────────────────────────────────

st.title("📊 Complete Community Transaction Network")
st.markdown("""
Visualizes all transactions within a community with **directed edges**.
Each node is an account, and arrows show transaction direction (from originator to beneficiary).
""")

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
    + chip("#FFD700", "Highlighted account")
)


# ── Step 1 — Risk typologies filter ───────────────────────────────────────────

st.subheader("1. Select risk typologies")
st.markdown(
    "Pick one or more risk types to restrict your search to communities that carry those risks. "
    "Leave empty to search across all communities."
)

selected_risk_typologies = st.multiselect(
    "Risk typologies",
    options=RISK_TYPE_OPTIONS,
    default=[],
    placeholder="All communities (no filter)",
    key="risk_typologies_filter",
    label_visibility="collapsed",
)

# When the filter changes, wipe the current search state and restart cleanly
if selected_risk_typologies != st.session_state["_prev_risk_filter"]:
    st.session_state["_prev_risk_filter"] = selected_risk_typologies
    reset_all()
    st.rerun()

st.divider()

# ── Step 2 — Input section ─────────────────────────────────────────────────────

st.subheader("2. Select Input Parameters")

# Get current input type
current_input_type = st.radio(
    "Select Input Type",
    options=["Entity Name", "Account Number", "Community ID"],
    help="Choose whether to enter an Entity Name, an Account Number or a Community ID,",
    horizontal=True,
    key="input_type_radio"
)

# Detect tab switch → wipe everything and rerun so the UI is clean
if st.session_state.last_input_type != current_input_type:
    reset_all()
    st.session_state.last_input_type = current_input_type
    st.rerun()

# Initialize input variables — populated below based on selected type
component_id = None
account_name = None             # display name (for labelling)
selected_acc_number = None      # resolved account number from name/account search
selected_community_index = None # community index read directly from the selected candidate

# Input fields based on selected type
if current_input_type == "Community ID":
    component_id = st.text_input(
        "Community ID",
        value="",
        help="Enter the community ID"
    )

    if component_id and component_id.strip():
        comm_risk_types = get_community_risk_types(component_id.strip())
        if comm_risk_types:
            st.info(
                f"Risk types in community **{component_id.strip()}**: "
                + " · ".join(f"**{r}**" for r in comm_risk_types.split(","))
            )
        if selected_risk_typologies:
            present = set(comm_risk_types.split(",")) if comm_risk_types else set()
            if not any(r in present for r in selected_risk_typologies):
                st.warning(
                    f"⚠️ Community **{component_id.strip()}** does not contain any of the "
                    f"selected risk types: {', '.join(selected_risk_typologies)}"
                )

elif current_input_type == "Entity Name":
    account_name_input = st.text_input(
        "Entity Name",
        value="",
        help=f"Type at least {MIN_SEARCH_CHARS} characters to see matching accounts"
    )

    candidates = search_names(account_name_input)

    if account_name_input and len(account_name_input) < MIN_SEARCH_CHARS:
        st.caption(f"Type at least {MIN_SEARCH_CHARS} characters to display matching candidates")
    elif account_name_input and not candidates:
        st.warning("No matching accounts found")
    elif candidates:
        if selected_risk_typologies:
            candidates = [
                c for c in candidates
                if any(r in c.get('risk_types', '').split(',') for r in selected_risk_typologies)
            ]
            if not candidates:
                st.warning(
                    f"No entities found in communities with risk type(s): "
                    f"{', '.join(selected_risk_typologies)}"
                )

        st.caption(f"**{len(candidates)}** candidate(s) found — select one to proceed")
        candidates_df = pd.DataFrame(candidates).rename(
            columns={
                "name": "Entity Name",
                "entity_id": "Entity ID",
                "acc_number": "Account Number",
                "community_index": "Community",
                "has_multiple_communities": "Entity has different account numbers",
                "community_size": "Community Size",
                "risk_count": "Flagged accounts in community",
                "risk_types": "Risk Types in Community",
            }
        )

        selection = st.dataframe(
            candidates_df,
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row"
        )

        selected_rows = selection.selection.rows
        if selected_rows:
            selected_candidate = candidates[selected_rows[0]]
            selected_acc_number = selected_candidate["acc_number"]
            selected_community_index = selected_candidate["community_index"]
            account_name = selected_candidate["name"]
            st.success(f"Selected: **{account_name}** (Account: {selected_acc_number})")
        else:
            st.caption("Click a row to select an account")

elif current_input_type == "Account Number":
    account_number_input = st.text_input(
        "Account Number",
        value="",
        help=f"Type at least {MIN_SEARCH_CHARS} characters to see matching accounts"
    )

    candidates = search_accounts(account_number_input)

    if account_number_input and len(account_number_input) < MIN_SEARCH_CHARS:
        st.caption(f"Type at least {MIN_SEARCH_CHARS} characters to display matching candidates")
    elif account_number_input and not candidates:
        st.warning("No matching accounts found")
    elif candidates:
        if selected_risk_typologies:
            candidates = [
                c for c in candidates
                if any(r in c.get('risk_types', '').split(',') for r in selected_risk_typologies)
            ]
            if not candidates:
                st.warning(
                    f"No accounts found in communities with risk type(s): "
                    f"{', '.join(selected_risk_typologies)}"
                )

        st.caption(f"**{len(candidates)}** candidate(s) found — select one to proceed")
        candidates_df = pd.DataFrame(candidates).rename(
            columns={
                "account": "Account Number",
                "name": "Entity Name",
                "entity_id": "Entity ID",
                "has_multiple_communities": "Account present in multiple communities",
                "community_index": "Community",
                "community_size": "Community Size",
                "flagged_count": "Flagged accounts in community",
                "risk_types": "Risk Types in Community",
            }
        )

        selection = st.dataframe(
            candidates_df,
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row"
        )

        selected_rows = selection.selection.rows
        if selected_rows:
            selected_candidate = candidates[selected_rows[0]]
            selected_acc_number = selected_candidate["account"]
            selected_community_index = selected_candidate["community_index"]
            st.success(
                f"Selected: **{selected_acc_number}** "
                f"(Community: {selected_community_index})"
            )
        else:
            st.caption("Click a row to select an account")

# n_hops is always read from session state; the slider only appears post-generate
n_hops = st.session_state.n_hops

# Reset results and UI when selecting another candidate or changing inputs
current_search_key = (current_input_type, component_id, selected_acc_number)
loaded_search_key = (
    st.session_state.loaded_inputs.get("input_type"),
    st.session_state.loaded_inputs.get("component_id"),
    st.session_state.loaded_inputs.get("selected_acc_number"),
)

if st.session_state.data_loaded and current_search_key != loaded_search_key:
    reset_results_only()

# ── Load button ────────────────────────────────────────────────────────────────
st.subheader("3. Generate Graph")

if st.button("🔍 Generate Community Graph", type="primary"):
    # Validate inputs before doing anything
    valid = True
    if current_input_type == "Community ID" and not (component_id or "").strip():
        st.error("Please enter a Community ID")
        valid = False
    elif current_input_type == "Entity Name" and not selected_acc_number:
        st.error("Please select an account from the candidates list")
        valid = False
    elif current_input_type == "Account Number" and not selected_acc_number:
        st.error("Please select an account from the candidates list")
        valid = False
    elif not os.path.exists(os.path.join(DATA_PATH, FILE_NAME)):
        st.error("Data file not found")
        valid = False

    if valid:
        reset_results_only()
        st.session_state.n_hops = n_hops

        gc.collect()

        # ── Resolve community component ───────────────────────────────────────
        # Community is read directly from the selected candidate row in the index —
        # no parquet scan needed.
        try:
            if current_input_type == "Community ID":
                st.session_state.community_component = component_id.strip()
                st.session_state.resolved_account_id = None
                st.session_state.resolved_account_name = None

            elif current_input_type == "Entity Name":
                st.session_state.community_component = selected_community_index
                st.session_state.resolved_account_id = selected_acc_number
                st.session_state.resolved_account_name = account_name

            elif current_input_type == "Account Number":
                st.session_state.community_component = selected_community_index
                st.session_state.resolved_account_id = selected_acc_number
                st.session_state.resolved_account_name = selected_acc_number

        except Exception as e:
            st.error(f"❌ Error resolving community: {e}")
            st.exception(e)
            st.stop()

        # ── Load data ──────────────────────────────────────────────────────────
        with st.spinner("Checking community size..."):
            try:
                too_large, entity_count= is_community_too_large(
                    DATA_PATH, st.session_state.community_component
                )

                st.session_state.entity_count = entity_count
                st.session_state.too_large= too_large 
            except Exception as e:
                st.error(f"❌ Error checking community size: {e}")
                st.exception(e)
                st.stop()
        # decide load mode:
        if too_large and current_input_type == "Community ID":
            st.session_state.awaiting_fallback = True
            st.session_state.use_subgraph = False
        elif too_large:
            st.session_state.use_subgraph = True
            st.session_state.awaiting_fallback = False
        else:
            st.session_state.use_subgraph = False
            st.session_state.awaiting_fallback = False
        
        # Load data (only when not waiting for fallback)
        if not st.session_state.awaiting_fallback:
            with st.spinner("loading transactions.."):
                try:
                    if st.session_state.use_subgraph:
                        df = load_subgraph_data(
                        path_data=DATA_PATH,
                        account_id=st.session_state.resolved_account_id,
                        n_hops=st.session_state.n_hops,
                        community_index=st.session_state.community_component
                    )
                    else:
                        df= load_community_data(
                            community_component=st.session_state.community_component,
                            path_data= DATA_PATH
                        )
                    st.session_state.df = df
                    st.session_state.loaded_inputs = {
                        "input_type": current_input_type,
                        "component_id": component_id,
                        "selected_acc_number": selected_acc_number,
                        "n_hops": st.session_state.n_hops,
                    }
                    if not df.empty:
                        _build_monthly_steps(df)
                        st.session_state.data_loaded = True
                        if too_large and not st.session_state.awaiting_fallback:
                            st.info(
                            f"ℹ️ Community **{st.session_state.community_component}** has "
                            f"**{entity_count:,}** entities (limit {MAX_ENTITIES_FULL_GRAPH:,}). "
                            f"Showing subgraph ({st.session_state.n_hops} hop(s))."
                        )
                        st.success(f"✅ Loaded {len(df):,} transactions")
                    else:
                        st.warning("No transactions found")


                except Exception as e:
                    st.error(f"❌ Error loading data: {e}")
                    st.exception(e)
                finally:
                    gc.collect()

# ── Fallback form (top-level, NOT nested inside button block) ─────────────────
# Rendered when Community ID input leads to an oversized community.

if st.session_state.awaiting_fallback:
    st.warning(
        f"⚠️ Community **{st.session_state.community_component}** contains "
        f"**{st.session_state.entity_count:,}** distinct entities "
        f"(limit: {MAX_ENTITIES_FULL_GRAPH:,}). "
        "Please use Entity name or Account Number search"
    )


# ── Display section ───────────────────────────────────────────────────────────
# Reads exclusively from session state — survives all reruns.
if st.session_state.data_loaded and st.session_state.df is not None:
    st.subheader("4. Graph Visualization")
    df = st.session_state.df

    # Date slider
    if st.session_state.monthly_steps:
        if st.session_state.selected_date not in st.session_state.monthly_steps:
            st.session_state.selected_date = st.session_state.monthly_steps[-1]

        if len(st.session_state.monthly_steps) > 1:
            selected_date = st.select_slider(
                "Community timeline (cumulative)",
                options=st.session_state.monthly_steps,
                value=st.session_state.selected_date,
                format_func=lambda d: d.strftime("%b %Y"),
                key="date_slider"
            )
            st.session_state.selected_date = selected_date
        else:
            st.info(f"Showing data for: {st.session_state.monthly_steps[0].strftime('%b %d, %Y')}")
            selected_date = st.session_state.monthly_steps[0]
    else:
        selected_date = None

    # Filter data by selected date
    if selected_date:
        filtered_df = df[df["EXECUTION_GLOBAL_DATE_TIME"].dt.date <= selected_date]
    else:
        filtered_df = df

    # ── n_hops slider — only shown when community was too large (subgraph mode) ──
    loaded_type = st.session_state.loaded_inputs.get("input_type")
    if st.session_state.too_large and loaded_type in ["Entity Name", "Account Number"]:
        new_hops = st.slider(
            "Number of hops (subgraph depth)",
            min_value=1,
            max_value=5,
            value=st.session_state.n_hops,
            help="Adjust subgraph depth around the searched account. Updates the graph immediately.",
            key="n_hops_slider"
        )
        if new_hops != st.session_state.n_hops:
            st.session_state.n_hops = new_hops
            with st.spinner(f"Reloading subgraph with {new_hops} hop(s)..."):
                try:
                    new_df = load_subgraph_data(
                        path_data=DATA_PATH,
                        account_id=st.session_state.resolved_account_id,
                        n_hops=new_hops,
                        community_index=st.session_state.community_component
                    )
                    st.session_state.df = new_df
                    st.session_state.loaded_inputs["n_hops"] = new_hops
                    if not new_df.empty:
                        _build_monthly_steps(new_df)
                    st.session_state["_graph_cache_key"] = None
                    gc.collect()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error reloading subgraph: {e}")
                    st.exception(e)

    # ── Metrics ───────────────────────────────────────────────────────────────
    col_info, col_graph = st.columns([0.3, 0.7])

    with col_info:
        unique_accounts = (
            set(filtered_df["ORIGINATOR_NEW_ACC_NUMBER"].dropna()) |
            set(filtered_df["BENEFICIARY_NEW_ACC_NUMBER"].dropna())
        )
        unique = len(unique_accounts)
        total_amount = filtered_df["BASE_CURR_AMOUNT"].sum()
        formatted_amount = format_amount(total_amount)

        st.subheader("📋 Community Information")

        if len(filtered_df) > 0:
            date_min = filtered_df["EXECUTION_GLOBAL_DATE_TIME"].min().date()
            date_max = filtered_df["EXECUTION_GLOBAL_DATE_TIME"].max().date()

            info_data = {
                "Metric": [
                    "Community ID",
                    "Display Mode",
                    "Unique Accounts",
                    "Total Transactions",
                    "Total Amount (EUR)",
                    "Date Range"
                ],
                "Value": [
                    f"{filtered_df['community_index'].iloc[0]}",
                    (
                        f"Subgraph ({st.session_state.n_hops} hop(s))"
                        if st.session_state.use_subgraph
                        else "Full community"
                    ),
                    f"{unique}",
                    f"{len(filtered_df):,}",
                    f"{formatted_amount}",
                    (
                        f"{date_min} to {date_max}"
                        if date_min != date_max
                        else date_min.strftime("%b %d, %Y")
                    )
                ]
            }

            # Account-specific rows — read from session state, not local variables
            resolved_id = st.session_state.resolved_account_id
            resolved_name = st.session_state.resolved_account_name
            loaded_type = st.session_state.loaded_inputs.get("input_type")

            if resolved_id and loaded_type == "Entity Name":
                orig_count = (filtered_df["ORIGINATOR_NEW_ACC_NUMBER"] == resolved_id).sum()
                bene_count = (filtered_df["BENEFICIARY_NEW_ACC_NUMBER"] == resolved_id).sum()
                info_data["Metric"].extend(["Searched Account", "Account Number", "Txn as Originator", "Txn as Beneficiary"])
                info_data["Value"].extend([
                    resolved_name or resolved_id,
                    resolved_id,
                    f"{orig_count:,}",
                    f"{bene_count:,}"
                ])

            elif resolved_id and loaded_type == "Account Number":
                orig_count = (filtered_df["ORIGINATOR_NEW_ACC_NUMBER"] == resolved_id).sum()
                bene_count = (filtered_df["BENEFICIARY_NEW_ACC_NUMBER"] == resolved_id).sum()
                info_data["Metric"].extend(["Searched Account Number", "Txn as Originator", "Txn as Beneficiary"])
                info_data["Value"].extend([
                    resolved_id,
                    f"{orig_count:,}",
                    f"{bene_count:,}"
                ])

            st.markdown(
                "<style>div[data-testid='stTable'] table {font-size: 12px;}</style>",
                unsafe_allow_html=True,
            )
            st.table(pd.DataFrame(info_data))

    # ── Graph ─────────────────────────────────────────────────────────────────
    with col_graph:
        with st.expander("Graph legend", expanded=False):
            st.markdown(legend_html, unsafe_allow_html=True)

        graph_cache_key = (
            st.session_state.community_component,
            st.session_state.resolved_account_id,
            st.session_state.n_hops,
            selected_date,
        )
        if st.session_state.get("_graph_cache_key") != graph_cache_key:
            with st.spinner("Generating graph visualization..."):
                highlight_id = st.session_state.resolved_account_id
                highlight_name = (
                    st.session_state.resolved_account_name
                    if st.session_state.loaded_inputs.get("input_type") == "Entity Name"
                    else None
                )
                graph_file = generate_community_graph(
                    filtered_df,
                    max_edges=MAX_EDGES_DISPLAY,
                    max_date=selected_date or filtered_df["EXECUTION_GLOBAL_DATE_TIME"].max().date(),
                    highlight_account_id=highlight_id,
                    highlight_account_name=highlight_name
                )
                if graph_file:
                    with open(graph_file, "r", encoding="utf-8") as f:
                        st.session_state.html_content = f.read()
                st.session_state["_graph_cache_key"] = graph_cache_key

        if st.session_state.html_content:
            st.components.v1.html(
                st.session_state.html_content,
                height=600,
                scrolling=True
            )

    # ── Download ──────────────────────────────────────────────────────────────
    if st.session_state.html_content and len(filtered_df) > 0:
        community_idx = filtered_df["community_index"].iloc[0]
        download_label = (
            f"community_{community_idx}_subgraph_{st.session_state.resolved_account_id}"
            f"_{st.session_state.n_hops}hops.html"
            if st.session_state.use_subgraph
            else f"community_{community_idx}_graph.html"
        )
        st.download_button(
            label="📥 Download Full Graph",
            data=st.session_state.html_content,
            file_name=download_label,
            mime="text/html"
        )

    if len(filtered_df) > 0:
        display_full_community_data(
            filtered_df,
            st.session_state.loaded_inputs.get("component_id") or filtered_df["community_index"].iloc[0]
        )

