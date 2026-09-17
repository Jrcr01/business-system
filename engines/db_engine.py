import os
import sqlite3
import pandas as pd
from datetime import datetime

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

def df_query(sql_query: str, params: tuple = ()) -> pd.DataFrame:
    """Executes SQL query and returns a Pandas DataFrame with NULLs coerced to empty strings."""
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(sql_query, conn, params=params)
        return df.fillna('')

def promote_draft_statement(statement_id: int, target_status: str) -> str:
    """Atomically transitions an Order Statement and assigns a permanent YYYY - OS XXXX sequence ID."""
    if target_status not in ('Issued',):
        raise ValueError("Target status must be 'Issued'.")

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT statement_ref, status FROM order_statements WHERE statement_id = ?", (statement_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Order Statement ID {statement_id} not found.")
            
        current_ref, current_status = row
        permanent_ref = current_ref

        if current_ref.startswith("DRAFT-") or current_status == "Draft":
            current_year = datetime.now().year
            prefix = f"{current_year} - OS "

            # Take the MAX sequence number actually in use, not "whichever row has the highest
            # statement_id" -- statement_id order and promotion order aren't the same thing (a
            # batch created earlier can be promoted later than one created after it), so the
            # previous "ORDER BY statement_id DESC LIMIT 1" could pick a lower sequence number
            # than one already assigned to an earlier-promoted-but-higher-statement_id batch,
            # producing a ref collision (UNIQUE constraint failed) on the very next promotion.
            cursor.execute("SELECT statement_ref FROM order_statements WHERE statement_ref LIKE ?", (f"{prefix}%",))
            existing_seqs = [int(suffix) for (ref,) in cursor.fetchall() if (suffix := ref.replace(prefix, "").strip()).isdigit()]
            next_seq = max(existing_seqs) + 1 if existing_seqs else 1

            permanent_ref = f"{prefix}{next_seq:04d}"

        cursor.execute("""
            UPDATE order_statements 
            SET statement_ref = ?, status = ? 
            WHERE statement_id = ?
        """, (permanent_ref, target_status, statement_id))
        
        conn.commit()
        return permanent_ref