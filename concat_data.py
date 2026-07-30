# ===================== NOTEBOOK 7 - Network Compliance Pipeline =====================

import pandas as pd
import numpy as np
import sys
import os
import statistics
from tqdm import tqdm
import itertools
from collections import defaultdict
from typing import Optional, Dict, Any
import warnings

from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.styles import PatternFill, Border, Side, Alignment
from openpyxl.styles.named_styles import NamedStyle

warnings.filterwarnings('ignore')

# ===================== CONFIGURATION =====================

PERIOD = '2026_03'
COUNTRY = 'US'
PREFIX = f"{PERIOD}_{COUNTRY}"
CHUNK_SIZE = 50000

# Paths
PATH_TRANSAC_FLAGS = '/domino/datasets/local/transactions_with_flags/'
PATH_PAIRS_FLAGS = '/domino/datasets/local/pairs_with_flags/'
PATH_PACKAGE = '/domino/datasets/local/investigation_package/'


# ===================== DATA LOADING FUNCTIONS =====================

def load_sampled_pairs(path_pairs_flags: str, prefix: str) -> tuple:
    """Load sampled pairs from CSV"""
    data_sampled_pairs = pd.read_csv(
        os.path.join(path_pairs_flags, f'{prefix}_sampled_pairs_PDQ.csv'),
        sep=','
    )
    sampled_pairs_list = data_sampled_pairs.Pair_ID.unique().tolist()
    return data_sampled_pairs, sampled_pairs_list


def load_all_pairs(path_pairs_flags: str, prefix: str, sampled_pairs_list: list) -> pd.DataFrame:
    """Load all pairs with flags"""
    data_pairs = pd.read_csv(
        os.path.join(path_pairs_flags, f'{prefix}_all_pairs_with_flags_PDQ.csv'),
        sep=';'
    )
    data_all_pairs_sampled = data_pairs[data_pairs['id_pairs'].isin(sampled_pairs_list)]
    return data_all_pairs_sampled


def load_transactions_by_chunk(
    path_transac_flags: str,
    prefix: str,
    chunk_size: int = 50000
) -> pd.DataFrame:
    """Load transaction data by chunks"""
    files_to_pick = [f for f in os.listdir(path_transac_flags) if f.startswith(prefix)]
    data = pd.DataFrame()
    
    for file_path in files_to_pick:
        full_path = os.path.join(path_transac_flags, file_path)
        for chunk in pd.read_csv(
            full_path,
            chunksize=chunk_size,
            na_values=["", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN",
                      "-nan", "1.#IND", "1.#QNAN", "<NA>", "N/A", "NULL", "NaN", "n/a", "nan", "null"],
            keep_default_na=False,
            delimiter=',',
            encoding='ISO-8859-1',
            decimal=',',
            thousands=' ',
            parse_dates=['TRANSACTION_DATE']
        ):
            data = pd.concat([data, chunk])
    
    return data


def prepare_transaction_data(data: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Prepare transaction data with id_pairs and flags"""
    data['id_pairs'] = data['FOCAL_ID'] + '-' + data['COUNTERPARTY_ID']
    data['id_pairs'] = data['id_pairs'].astype(str)
    
    # Define flag columns
    flag_columns = [
        'diff_fcl_bank_countries', 'diff_ctpy_bank_countries',
        'WS_fcl', 'WS_ctpy',
        'HS_fcl', 'HS_ctpy',
        'IHV_fcl', 'IHV_ctpy',
        'check_omc',
        'opaque_fcl', 'opaque_ctpy',
        'psp_fcl', 'psp_ctpy',
        'msb_fcl_hit', 'msb_ctpy_hit', 'msb_mess_hit',
        'digital_asset_fcl_all', 'digital_asset_ctpy_all', 'digital_asset_mess_name',
        'TF_HT_OFC_hit',
        'PORO_COBO_hit',
        'fincen_hit',
        'HAS_NESTING_ORIG', 'HAS_NESTING_BENE'
    ]
    
    # Create mask of flags
    mask = data[flag_columns].values == 1
    col_names = data[flag_columns].columns
    data['flags_involved'] = [', '.join(col_names[mask[i]]) for i in range(len(data))]
    data['nb_flags'] = data[flag_columns].sum(axis=1)
    
    return data


def filter_and_rename_pairs(
    data_all_pairs_sampled: pd.DataFrame,
    pairs_to_investigation_list: list
) -> pd.DataFrame:
    """Filter pairs for investigation and rename columns"""
    data_red = data_all_pairs_sampled[data_all_pairs_sampled['id_pairs'].isin(pairs_to_investigation_list)]
    data_red['risk_signal_id'] = prefix + '_' + data_red['id_pairs']
    
    return data_red


def reorder_dataframe_columns(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Reorder columns in dataframe"""
    colonnes = list(df.columns)
    
    # 1. Move 'risk_signal_id' after 'ALERT_DATE'
    colonnes.remove('risk_signal_id')
    position_alert_date = colonnes.index('TRANSACTION_DATE')
    colonnes.insert(position_alert_date + 1, 'risk_signal_id')
    
    # 2. Move 'id_pairs' after 'risk_signal_id'
    colonnes.remove('id_pairs')
    position_risk_signal_id = colonnes.index('risk_signal_id')
    colonnes.insert(position_risk_signal_id + 1, 'id_pairs')
    
    # 3. Move 'nb_flags' after 'id_pairs'
    colonnes.remove('nb_flags')
    position_id_pairs = colonnes.index('id_pairs')
    colonnes.insert(position_id_pairs + 1, 'nb_flags')
    
    # 4. Move 'flags_involved' after 'nb_flags'
    colonnes.remove('flags_involved')
    position_nb_flags = colonnes.index('nb_flags')
    colonnes.insert(position_nb_flags + 1, 'flags_involved')
    
    # 5. Move 'TF_HT_OFC_hit' before 'TF_HT_OFC_keyword'
    if 'TF_HT_OFC_hit' in colonnes and 'TF_HT_OFC_keyword' in colonnes:
        colonnes.remove('TF_HT_OFC_hit')
        position_keyword = colonnes.index('TF_HT_OFC_keyword')
        colonnes.insert(position_keyword, 'TF_HT_OFC_hit')
    
    df_red = df[colonnes]
    return df_red


def sort_and_convert_data(df: pd.DataFrame) -> pd.DataFrame:
    """Sort and convert data types"""
    df_all_pairs_sampled = df.sort_values(by='id_pairs')
    df_red = df_red.sort_values(by='id_pairs')
    
    # Convert columns to numeric
    df_red['BASE_AMOUNT'] = pd.to_numeric(df_red['BASE_AMOUNT'], errors='coerce')
    df_red['ORIGINATOR_AMOUNT'] = pd.to_numeric(df_red['ORIGINATOR_AMOUNT'], errors='coerce')
    
    return df_all_pairs_sampled, df_red


# ===================== EXCEL EXPORT FUNCTIONS =====================

def build_file_path(path_package: str, prefix: str) -> str:
    """Build full file path for Excel export"""
    file_name = f"{prefix}_investigation_package.xlsx"
    full_path = os.path.join(path_package, file_name)
    return full_path


def write_excel_workbook(df_all_pairs_sampled: pd.DataFrame, df_red: pd.DataFrame, full_path: str):
    """Write Excel workbook with sheets"""
    with pd.ExcelWriter(full_path, engine='openpyxl') as writer:
        df_all_pairs_sampled.to_excel(writer, sheet_name='pairs', index=False)
        df_red.to_excel(writer, sheet_name='transactions', index=False)
    
    # Get workbook for additional formatting
    workbook = writer.book
    ws_pairs = writer.sheets['pairs']
    ws_trans = writer.sheets['transactions']
    
    return workbook, ws_pairs, ws_trans


def adjust_column_widths(ws):
    """Adjust column widths based on content"""
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = (max_length + 2) * 1.2
        ws.column_dimensions[column].width = adjusted_width


def create_excel_styles() -> Dict[str, Any]:
    """Create Excel styling objects"""
    header_fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    return {
        'header_fill': header_fill,
        'thin_border': thin_border
    }


def apply_header_and_borders(ws, styles: Dict[str, Any]):
    """Apply header styling and borders"""
    header_fill = styles['header_fill']
    thin_border = styles['thin_border']
    
    for cell in ws[1]:
        cell.fill = header_fill
        cell.border = thin_border
    
    for row in ws.iter_rows():
        for cell in row:
            cell.border = thin_border


def format_amount_columns(ws, df_columns: list, column_name: str):
    """Format amount columns with thousand separators"""
    try:
        col_index = list(df_columns).index(column_name) + 1
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=col_index, max_col=col_index):
            for cell in row:
                cell.number_format = '#,##0.00'
    except ValueError:
        pass


def export_to_excel(df_all_pairs_sampled: pd.DataFrame, df_red: pd.DataFrame, full_path: str):
    """Complete Excel export with formatting"""
    write_excel_workbook(df_all_pairs_sampled, df_red, full_path)
    
    workbook = writer.book
    ws_pairs = writer.sheets['pairs']
    ws_trans = writer.sheets['transactions']
    
    styles = create_excel_styles()
    
    # Apply formatting to pairs sheet
    adjust_column_widths(ws_pairs)
    apply_header_and_borders(ws_pairs, styles)
    format_amount_columns(ws_pairs, df_all_pairs_sampled.columns, 'sum_of_transaction_amounts')
    
    # Apply formatting to transactions sheet
    adjust_column_widths(ws_trans)
    apply_header_and_borders(ws_trans, styles)
    format_amount_columns(ws_trans, df_red.columns, 'BASE_AMOUNT')
    format_amount_columns(ws_trans, df_red.columns, 'ORIGINATOR_AMOUNT')
    
    ws_trans.auto_filter.ref = ws_trans.dimensions
    ws_pairs.auto_filter.ref = ws_pairs.dimensions
    
    workbook.save(full_path)
    print(f"✅ Excel file saved to: {full_path}")


# ===================== MAIN PIPELINE =====================

def main():
    """Main execution function"""
    # Load sampled pairs
    data_sampled_pairs, sampled_pairs_list = load_sampled_pairs(PATH_PAIRS_FLAGS, PREFIX)
    
    # Load all pairs
    data_all_pairs_sampled = load_all_pairs(PATH_PAIRS_FLAGS, PREFIX, sampled_pairs_list)
    
    # Load SQL pairs (if using)
    SQL_list = []
    pairs_to_investigation_list = SQL_list + sampled_pairs_list
    
    # Load transactions by chunk
    data = load_transactions_by_chunk(PATH_TRANSAC_FLAGS, PREFIX, CHUNK_SIZE)
    
    # Prepare transaction data
    data = prepare_transaction_data(data, PREFIX)
    
    # Filter and rename pairs
    data_red = filter_and_rename_pairs(data_all_pairs_sampled, pairs_to_investigation_list)
    
    # Rename columns for clarity
    data_all_pairs_sampled.rename(
        columns={'MESSAGE_KEY': 'nb_transactions', 'BASE_AMOUNT': 'sum_of_transaction_amounts'},
        inplace=True
    )
    
    # Reorder columns
    data_all_pairs_sampled = data_all_pairs_sampled.drop(['WS_HS_involved', 'bucket'], axis=1, inplace=True, axis=1)
    data_all_pairs_sampled = reorder_dataframe_columns(data_all_pairs_sampled, PREFIX)
    
    # Sort and convert
    data_all_pairs_sampled, data_red = sort_and_convert_data(data_all_pairs_sampled, data_red)
    
    # Export to Excel
    full_path = build_file_path(PATH_PACKAGE, PREFIX)
    export_to_excel(data_all_pairs_sampled, data_red, full_path)


if __name__ == '__main__':
    main()
