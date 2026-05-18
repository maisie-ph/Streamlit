from utils.get_node_color import get_node_colors_batch
import networkx as nx
from pyvis.network import Network
import pandas as pd
import gc
import logging
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# Logging config
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)    


def generate_community_graph(df,
    output_file="community_graph.html",
    max_edges=5000,
    highlight_account_id= None,
    highlight_account_name=None,
    highlight_bic = None,
    max_date= None
):

    """
    Generate community graph with memory optimization.

    Args:
        df: DataFrame with transaction data
        output_file: Output HTML file path
        max_edges: Maximum number of edges to display (to prevent crashes)
    """

    if max_date is not None:
        df=df[df["EXECUTION_GLOBAL_DATE_TIME"].dt.date <= max_date]
    if df.empty:
        logger.info("No data to visualize")
        return None

    logger.info(f"Generating graph from {len(df):,} transactions...")

    # Creating directed graph
    G = nx.DiGraph()

    # Get unique accounts by ACC NUMBER
    originators = df['ORIGINATOR_NEW_ACC_NUMBER'].unique()
    beneficiaries = df['BENEFICIARY_NEW_ACC_NUMBER'].unique()
    unique_accounts = set(originators) | set(beneficiaries)

     
    # Also get Entity Names if we have them in the dataframe
    if 'ORIGINATOR_NAME' in df.columns and 'BENEFICIARY_NAME' in df.columns:
        originator_names = df['ORIGINATOR_NAME'].unique()
        beneficiary_names = df['BENEFICIARY_NAME'].unique()
        unique_names = set(originator_names) | set(beneficiary_names)
        unique_names.discard(None)
        unique_names = {name for name in unique_names if pd.notna(name)}
    else:
        unique_names = set()

    # Remove NaN
    unique_accounts.discard(None)
    unique_accounts = {acc for acc in unique_accounts if pd.notna(acc)}

    logger.info(f"Found {len(unique_accounts)} unique accounts")


    # Free memory
    del originators, beneficiaries
    if 'originator_names' in locals():
        del originator_names, beneficiary_names
    gc.collect()
    
    # Precompute all node colors in one vectorized pass
    node_colors = get_node_colors_batch(df)

    # Create a mapping of Entity Names to IDs
    name_to_id = {}
    if highlight_account_name and 'ORIGINATOR_NAME' in df.columns:
        _orig_pairs = df[['ORIGINATOR_NAME', 'ORIGINATOR_NEW_ACC_NUMBER']].dropna()
        _bene_pairs = df[['BENEFICIARY_NAME', 'BENEFICIARY_NEW_ACC_NUMBER']].dropna()
        _bene_pairs.columns = ['ORIGINATOR_NAME', 'ORIGINATOR_NEW_ACC_NUMBER']
        name_to_id = (
            pd.concat([_orig_pairs, _bene_pairs], ignore_index=True)
            .drop_duplicates('ORIGINATOR_NAME')
            .set_index('ORIGINATOR_NAME')['ORIGINATOR_NEW_ACC_NUMBER']
            .to_dict()
        )

    ## Create a reverse dictionary ( ID to name) to display entities's name on hover
    id_to_name = {}

    if 'ORIGINATOR_NAME' in df.columns:
        orig = df[['ORIGINATOR_NEW_ACC_NUMBER', 'ORIGINATOR_NAME']].dropna().drop_duplicates('ORIGINATOR_NEW_ACC_NUMBER')
        id_to_name.update(
            orig.set_index(orig['ORIGINATOR_NEW_ACC_NUMBER'].astype(str).str.strip())['ORIGINATOR_NAME'].str.strip().to_dict()
        )

    if 'BENEFICIARY_NAME' in df.columns:
        orig = df[['BENEFICIARY_NEW_ACC_NUMBER', 'BENEFICIARY_NAME']].dropna().drop_duplicates('BENEFICIARY_NEW_ACC_NUMBER')
        id_to_name.update(
            orig.set_index(orig['BENEFICIARY_NEW_ACC_NUMBER'].astype(str).str.strip())['BENEFICIARY_NAME'].str.strip().to_dict()
        )


    # Determine which account to highlight
    account_to_highlight = None
    if highlight_account_id and str(highlight_account_id) in unique_accounts:
        account_to_highlight = highlight_account_id
    elif highlight_account_name and highlight_account_name in name_to_id:
        account_to_highlight = name_to_id[highlight_account_name]

    logger.info(f"Account to highlight: {account_to_highlight}")

    bic_accounts = set()
    if highlight_bic:
        bic_upper = highlight_bic.strip().upper()
        if 'BIC_ORIGINATOR' in df.columns:
            orig_bic = df[df['BIC_ORIGINATOR'].str.strip().str.upper() == bic_upper]['ORIGINATOR_NEW_ACC_NUMBER'].dropna().unique()
            bic_accounts.update(str(a).strip() for a in orig_bic)
        if 'BIC_BENEFICIARY' in df.columns:
            bene_bic = df[df['BIC_BENEFICIARY'].str.strip().str.upper() == bic_upper]['BENEFICIARY_NEW_ACC_NUMBER'].dropna().unique()
            bic_accounts.update(str(b).strip() for b in bene_bic)
        logger.info(f"Found {len(bic_accounts)} accounts linked to BIC {bic_upper}")


    # Adding nodes (one per account) with minimal attributes
    for account in unique_accounts:
        color = node_colors.get(str(account).strip(), '#DBDBDB')
        size = 15

        is_highlighted = str(account) == str(account_to_highlight)
        is_bic_account = str(account).strip() in bic_accounts

        if is_highlighted:
            border_width = 5
            border_color = '#FFD700' # Yellow for searched account 
        elif is_bic_account:
            border_width = 5
            border_color = '#B90DDC' # Violet for bic-related account 
        else:
            border_width = 1
            border_color = color
            
        display_name = id_to_name.get(str(account).strip())
        label = display_name if display_name else str(account)

        if display_name:
            hover_text = f"ACC NUMBER : {account}"
        else:
            hover_text = str(account)

        # Add BIC info to hover if account is linked to searched BIC 
        if is_bic_account and highlight_bic:
            hover_text += f"\n BIC: {highlight_bic.strip().upper()}"



        G.add_node(
            str(account),
            size=size,
            sizeselected= 15,
            color={
                "background": color, 
                "border": border_color,
                "highlight": {
                    "background":color,
                    "border": border_color
                }},
            borderWidth=border_width,
            borderWidthSelected=5,
            title = hover_text,
            label = label
        )

    # Aggregate edges before adding to graph (to improve memory efficiency)
    logger.info("Aggregating transaction data...")

    # Use convert dtypes to avoid issues with categorical
    df_copy = df.copy()

    if df_copy['ORIGINATOR_NEW_ACC_NUMBER'].dtype.name == 'category':
        df_copy['ORIGINATOR_NEW_ACC_NUMBER'] = df_copy['ORIGINATOR_NEW_ACC_NUMBER'].astype(str)
    if df_copy['BENEFICIARY_NEW_ACC_NUMBER'].dtype.name == 'category':
        df_copy['BENEFICIARY_NEW_ACC_NUMBER'] = df_copy['BENEFICIARY_NEW_ACC_NUMBER'].astype(str)

    edge_data = df_copy.groupby(
        ['ORIGINATOR_NEW_ACC_NUMBER', 'BENEFICIARY_NEW_ACC_NUMBER'],
        as_index=False
    ).agg({
        'BASE_CURR_AMOUNT': ['sum', 'count']
    })

    edge_data.columns = ['source', 'target', 'total_amount', 'transaction_count']

    # Free original dataframe copy
    del df_copy
    gc.collect()

    logger.info(f"Aggregated into {len(edge_data)} unique edges")

    # If too many edges warn and sample
    if len(edge_data) > max_edges:
        logger.warn(f"⚠️ Warning: {len(edge_data)} edges exceed limit of {max_edges}")
        logger.info(f"Keeping top {max_edges} edges by transaction volume...")
        edge_data = edge_data.nlargest(max_edges, 'total_amount')

    # Add edges with optimized attributes
    edge_data = edge_data.dropna(subset=['source', 'target'])
    G.add_edges_from(
        (
            str(row.source),
            str(row.target),
            {
                'title': f"{int(row.transaction_count)} txns | {row.total_amount:,.0f} EUR",
                'width': 5.0,
                'color': '#999999',
            }
        )
        for row in edge_data.itertuples(index=False)
    )

    logger.info(f"Graph created: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # Free edge data
    del edge_data
    gc.collect()

    # Create visualization with optimized settings
    net = Network(
        height="500px",
        width="100%",
        notebook=False,
        bgcolor="#F8F9FA",
        font_color="#333333",
        directed=True
    )

    net.from_nx(G)

    # Optimized physics settings for performance
    net.set_options("""
    {
    "nodes": {
        "font": {
            "size" :20,
            "face":"arial",
            "color": "#000000",
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
        "arrows": {
          "to": {
            "enabled": true,
            "scaleFactor": 0.5
          }
        },
        "smooth": {
          "type": "curvedCW",
          "roundness": 0.15
        }
      },
      "interaction": {
        "dragNodes": true,
        "hideEdgesOnDrag": false,
        "tooltipDelay": 200
      }
}
""")
    
    logger.info(f"Saving graph to {output_file}...")
    net.save_graph(output_file)
    logger.info(f"Graph saved in {output_file}")


    # Free memory
    del net, G
    gc.collect()

    return output_file

