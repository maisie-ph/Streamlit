import streamlit as st


# Page configuration 
st.set_page_config(
    page_title="📖 Overview – Risk by Extension Tool",
    page_icon="🚀",
    layout="wide",
)


# Load the global stylesheet 
with open("styles.css") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


st.markdown(
    """
    <style>
    .block-container {
    max-width: 1200px;
    padding-left: 2rem;
    padding-right: 2rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# Large main title
st.markdown(
    """
    <h1 style="text-align: center; font-size:2.8rem; color:#2E86C1; margin-bottom:0.6rem; margin-top:0.6rem">
         Risk by Extension Tool – Overview
    </h1>
    """,
    unsafe_allow_html=True,
)


# Subtitle
st.markdown(
    """
    <h3 style="text-align: center;font-size:1.6rem; color:#5D6D7E; margin-top:0.1rem;">
        Here are all the features you can find in the dashboards
    </h3>
    """,
    unsafe_allow_html=True,
)


# Body 
st.markdown(
    """
    <style>
        .big-p {
        font-size:1.15rem; 
        line-height:1.6;
        }
        .emoji-list li {
            margin-bottom:0.4rem;
        }
    </style>
    <div class="big-p">
    """,
    unsafe_allow_html=True,
)



st.header("🎯 What the dashboards let you do")
st.markdown(
"""
The dashboard lets you explore the transaction graph at three entry points:

1. **🔎 Search by Entity name / Entity account number / Community index** — to visualize one entity's activitites in a community.
1. **🏦 Search by BIC code** — search and visualize all the transactions involving risky Banks.
1. **📊 Visualize a whole community** — provide a Community ID and see every transaction as a directed graph.
1. **📈 Instant metrics** — unique accounts, total number of transactions, aggregate amount (EUR) and the date range appear at a glance.
1. **🧩 Interactive graph** — hover for tool‑tips, zoom/pan, download the full HTML graph, and optionally highlight the searched account.
1. **📋 Full data view** — an expandable table shows every raw transaction (downloadable as CSV if needed).

"""
)




st.header("⚙️ How to start")

st.markdown(
"""

- 1️⃣ **Open the sidebar** → select **🌐 Graph Vizualisation** or **🏦 Search by BIC code**.  
- 2️⃣ Choose the input type you need (Community ID, Entity ID, Entity Name, Entity Account Number or BIC code).
- 3️⃣ Press **🔍 Generate Full Community Graph**.
  """
  )




# Footer
st.markdown("---")

