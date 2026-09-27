import sqlite3
import pandas as pd
from datetime import datetime

DB_PATH = "ap_disbursement.db"

def df_query(sql_query: str, params: tuple = ()) -> pd.DataFrame:
    """
    Executes a SQL query and returns a Pandas DataFrame with NULL values 
    coerced to clean empty strings to prevent Streamlit rendering issues.
    """
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(sql_query, conn, params=params)
        return df.fillna('')

def promote_draft_statement(statement_id: int, target_status: str) -> str:
    """
    Atomically transitions an Order Statement from 'Draft' to 'Pending Review' or 'Issued',
    minting a permanent sequential 'YYYY - OS XXXX' ID only upon status change.
    """
    if target_status not in ('Pending Review', 'Issued'):
        raise ValueError("Target status must be 'Pending Review' or 'Issued'.")

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # 1. Retrieve current statement state
        cursor.execute(
            "SELECT statement_ref, status FROM order_statements WHERE statement_id = ?",
            (statement_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Order Statement ID {statement_id} not found.")
            
        current_ref, current_status = row
        permanent_ref = current_ref

        # 2. Mint permanent sequence if transitioning out of Draft
        if current_ref.startswith("DRAFT-") or current_status == "Draft":
            current_year = datetime.now().year
            prefix = f"{current_year} - OS "
            
            # Query highest existing sequence number for the current calendar year
            cursor.execute("""
                SELECT statement_ref FROM order_statements 
                WHERE statement_ref LIKE ? 
                ORDER BY statement_id DESC LIMIT 1
            """, (f"{prefix}%",))
            
            last_entry = cursor.fetchone()
            if last_entry:
                last_seq_str = last_entry[0].replace(prefix, "").strip()
                next_seq = int(last_seq_str) + 1 if last_seq_str.isdigit() else 1
            else:
                next_seq = 1
                
            permanent_ref = f"{prefix}{next_seq:04d}"

        # 3. Execute atomic update
        cursor.execute("""
            UPDATE order_statements 
            SET statement_ref = ?, status = ? 
            WHERE statement_id = ?
        """, (permanent_ref, target_status, statement_id))
        
        conn.commit()
        return permanent_ref