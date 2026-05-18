
import streamlit as st
import pandas as pd

# ── Helper: build monthly timeline steps from a dataframe ─────────────────────
def _build_monthly_steps(df):
    min_date = df["EXECUTION_GLOBAL_DATE_TIME"].min().date()
    max_date = df["EXECUTION_GLOBAL_DATE_TIME"].max().date()


    if min_date == max_date:
        st.session_state.monthly_steps = [min_date]
    else:
        steps = pd.date_range(
            start=min_date.replace(day=1),
            end=max_date,
            freq="ME"
        ).date.tolist()
        if steps and steps[-1] < max_date:
            steps.append(max_date)
        st.session_state.monthly_steps = steps
    if st.session_state.monthly_steps:
        st.session_state.selected_date = st.session_state.monthly_steps[-1]
    else:
        st.session_state.selected_date = None  # Handle the empty case appropriately
