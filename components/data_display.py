import streamlit as st
import pandas as pd
from io import BytesIO


def display_full_community_data(df, community_id):
    """
    Display all data for the selected community component
    
    Args:
        df: DataFrame with transaction data
        community_id: id of the community being analyzed
    """
    st.subheader(f"📄 Full Data for Community {community_id}")

    with st.expander("View All Community Data (Click to expand)"):
        # Show basic info first
        st.write(f"Total rows: {len(df):,}")

        st.markdown("### Data Preview")
        st.write("Showing max first 100 rows:")

        # Display all columns in the preview
        st.dataframe(
            df.head(100),
            column_config={
                "BASE_CURR_AMOUNT": st.column_config.NumberColumn(
                    "Amount (EUR)",
                    format="%.2f"
                ),
                "EXECUTION_GLOBAL_DATE_TIME": st.column_config.DateColumn(
                    "Transaction Date"
                )
            },
            hide_index=True,
            width='stretch'
        )

        st.markdown("### Download Options")

        # Create download buttons
        col1, col2 = st.columns(2)

        with col1:
            # CSV download
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download as CSV",
                data=csv,
                file_name=f"Community_{community_id}_full_data.csv",
                mime="text/csv"
            )

        with col2:
            # Excel download
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Community Data')
            excel_data = output.getvalue()

            st.download_button(
                label="📥 Download as Excel",
                data=excel_data,
                file_name=f"community_{community_id}_full_data.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
