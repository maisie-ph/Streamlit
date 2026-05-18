import streamlit as st
import polars as pl
import ast
import networkx as nx
import json
import os
from pyvis.network import Network

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

with open("config.json", "r") as f:
    _config = json.load(f)
DATA_PATH = _config["DATA_PATH"]
MAIN_PARQUET = os.path.join(DATA_PATH, _config["FILE_NAME"])
ALERTS_PARQUET = os.path.join(DATA_PATH, "scc_alerts_community_test.parquet")
CYCLE_GRAPH_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cycle_graph.html")

st.title("Alerts")

alerts = pl.read_parquet(ALERTS_PARQUET).to_pandas()
tab1,tab2, tab3 = st.tabs(["🔴Cycles", "📊 Structuring", "➡️ Pass-through"])

with tab1:
  st.dataframe(alerts[["community_id", "nb_entities", "nb_transactions", "total_amount"]])
  selected_idx = st.number_input("Alert index", min_value=0, max_value=len(alerts)-1, step=1)
  row = alerts.iloc[selected_idx]
  entity_ids = [str(e) for e in ast.literal_eval(row["NEW_ACC_NUMBERs"])]

  # ── FETCH NAMES & ACCOUNTS FROM PARQUET ──────────

  orig = (
    pl.scan_parquet(MAIN_PARQUET)
    .filter(pl.col("community_index") == float(row["community_id"]))
    .filter(pl.col("ORIGINATOR_NEW_ACC_NUMBER").cast(pl.Utf8).is_in(entity_ids))
    .select([
      pl.col("ORIGINATOR_ENTITY_ID").cast(pl.Utf8).alias("entity_id"),
      pl.col("ORIGINATOR_NAME").alias("name"),
      pl.col("ORIGINATOR_NEW_ACC_NUMBER").alias("account"),
      ])
      )

  bene = (
    pl.scan_parquet(MAIN_PARQUET)
    .filter(pl.col("community_index") == float(row["community_id"]))
    .filter(pl.col("BENEFICIARY_NEW_ACC_NUMBER").cast(pl.Utf8).is_in(entity_ids))
    .select([
      pl.col("BENEFICIARY_ENTITY_ID").cast(pl.Utf8).alias("entity_id"),
      pl.col("BENEFICIARY_NAME").alias("name"),
      pl.col("BENEFICIARY_ACC_NUMBER").alias("account"),
      ])
      )

  meta = pl.concat([orig, bene]).unique(subset=["entity_id"]).collect()

  name_map    = dict(zip(meta["account"].to_list(), meta["name"].to_list()))
  account_map = dict(zip(meta["account"].to_list(), meta["entity_id"].to_list()))

  # ── ENTITY TABLE ─────────────────────────────────

  st.subheader("Entities in Cycle")
  st.dataframe(meta.to_pandas()[["name", "account", "entity_id"]], use_container_width=True)

  # ── CYCLE PATH ───────────────────────────────────

  names = [name_map.get(eid, eid) for eid in entity_ids]
  st.info(" → ".join(names) + " → " + names[0])

  # ── GRAPH ────────────────────────────────────────

  if st.button("See on Graph"):
    df = (
      pl.scan_parquet(MAIN_PARQUET)
      .filter(pl.col("community_index") == float(row["community_id"]))
      .filter(
        pl.col("ORIGINATOR_NEW_ACC_NUMBER").cast(pl.Utf8).is_in(entity_ids) &
        pl.col("BENEFICIARY_NEW_ACC_NUMBER").cast(pl.Utf8).is_in(entity_ids)
        )
        .select(["ORIGINATOR_NEW_ACC_NUMBER", "BENEFICIARY_NEW_ACC_NUMBER", "BASE_CURR_AMOUNT"])
        .collect()
        )

    G = nx.DiGraph()
    for eid in entity_ids:
      G.add_node(
          eid,
          label=name_map.get(eid, eid),
          color="#DBDBDB",
          title=f"ID: {eid}"
      )

    for r in df.iter_rows(named=True):
      src, dst = str(r["ORIGINATOR_NEW_ACC_NUMBER"]), str(r["BENEFICIARY_NEW_ACC_NUMBER"])
      G.add_edge(src, dst, title=f"{r['BASE_CURR_AMOUNT']:,.0f}", color="#999999", width=3)

    net = Network(
        height="500px",
        notebook=False,
        bgcolor="#F8F9FA",
        font_color="#333333",
        directed=True
    )
    net.from_nx(G)
    net.set_options("""
{
  "nodes": {
      "font": {
          "size": 20,
          "face": "arial",
          "color": "#333333",
          "mod": "bold"
      }
  },
  "physics": {
      "barnesHut": {
        "gravitationalConstant": -50000,
        "centralGravity": 0.01,
        "springLength": 250,
        "springConstant": 0.02,
        "damping": 0.85,
        "avoidOverlap": 1
      },
      "maxVelocity": 40,
      "minVelocity": 2,
      "solver": "barnesHut",
      "timestep": 0.5,
      "adaptiveTimestep": true,
      "stabilization": {
        "enabled": true,
        "iterations": 250,
        "updateInterval": 25,
        "fit": true
      }
  },
  "edges": {
    "color": "#999999",
    "arrows": {"to": {"enabled": true, "scaleFactor": 0.5}},
    "smooth": {"type": "curvedCW", "roundness": 0.15}
  },
  "interaction": {
    "dragNodes": true,
    "hideEdgesOnDrag": false,
    "tooltipDelay": 200
  }
}
""")
    net.save_graph(CYCLE_GRAPH_HTML)
    st.session_state["cycle_graph_ready"] = True

  if st.session_state.get("cycle_graph_ready") and os.path.exists(CYCLE_GRAPH_HTML):
    with open(CYCLE_GRAPH_HTML) as f:
        st.components.v1.html(f.read(), height=600, scrolling=True)

with tab2:
  st.markdown("This section will be available soon")

with tab3:
  st.markdown("This section will be available soon")
