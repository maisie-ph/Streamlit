"""
Static help page for the AML Network Analytics dashboard.
Drop this file into your `pages/` directory and Streamlit will auto-register it
in the sidebar.
"""

import streamlit as st


with open("styles.css") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

st.markdown(
    """
    <style>
    .block-container {
    max-width: 1200px;
    padding-left: 5rem;
    padding-right: 5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.set_page_config(page_title="Help — Risk by extension tool", layout="centered")

st.title("📖 Help & Tips")
st.caption("Common ways to use the dashboard, plus a few tricks worth knowing.")



# ── Quick start ──────────────────────────────────────────────────────────────

st.header("Quick start")
st.markdown(
"""
The dashboard lets you explore the transaction graph at three entry points:

1. **Account Number** — jump straight to a specific entity and look at its neighborhood.
1. **Account Name** — fuzzy lookup when you don’t have the account number handy.
1. **Community ID** — load every entity that belongs to a given community.


Pick one, hit **Search**, and the directed graph renders below with a
downloadable HTML version and a full data table underneath.
"""
)

      

st.info(
"An entity flagged on multiple lists will only show its first flag's "
"color. Use the data table below the graph to see the full flag breakdown."
)

st.markdown(
"""
**Edges** are directed — the arrow goes from originator to beneficiary.
"""
)



# ── Working with the giant component ─────────────────────────────────────────

st.header("The giant component")

st.warning(
"This community contains ~99% of all entities. Loading it as a whole graph "
"will crash the app. The dashboard forces you into **subgraph mode** here."
)

st.markdown(
"""
When it is selected, or when any community exceeds **1,000 entities**,
the dashboard switches to ego-subgraph mode:

- Enter an **anchor account** (ID or name).
- Pick the number of **hops** (1 to 5) to expand around it.
- Only that ego-subgraph is loaded 

**Rule of thumb:** start at 1 hop, then expand. Going from 3 to 4 hops can
multiply node count by 10× or more in dense neighborhoods.
"""
)




# ── Tricks ───────────────────────────────────────────────────────────────────

st.header("Tricks worth knowing")

st.markdown(
"""

- **Re-running with the same parameters is free.** The data loader is cached
  in memory, so subsequent searches on the same community/account
  return instantly.
- **The HTML download is self-contained.** You can email it or attach it to
  a case file — it opens in any browser without a
  server.
- **Large subgraphs render slowly in the browser** If a
  3-hop expansion freezes your tab, you should drop to 2 hops or filter the date range first.
  """
  )


# ── Troubleshooting ──────────────────────────────────────────────────────────

st.header("Troubleshooting")

st.markdown(
"""

|Symptom                    |Likely cause                       |Fix                                                  |
|---------------------------|-----------------------------------|-----------------------------------------------------|
|App freezes on render      |Subgraph too large                 |Reduce hops or narrow the date window                |
|Account name not found     |Fuzzy match threshold too strict   |Try the partial account number, or copy-paste from the data table|
|Stale numbers in the header|Cached load                        |Clear cache from the top-right menu                  |
"""     
)                        



