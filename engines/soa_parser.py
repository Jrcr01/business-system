import io
import re
import pandas as pd
import pdfplumber
from PIL import Image
import pytesseract

def parse_soa_document(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """Parses PDF or Image files into a DataFrame containing doc_ref and soa_amount."""
    fname_lower = file_name.lower()
    raw_text = ""

    if fname_lower.endswith('.pdf'):
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text_lines = [page.extract_text() for page in pdf.pages if page.extract_text()]
            raw_text = "\n".join(text_lines)
    elif fname_lower.endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp')):
        image = Image.open(io.BytesIO(file_bytes)).convert('L')
        raw_text = pytesseract.image_to_string(image)

    parsed_records = []
    pattern = re.compile(r'([A-Z0-9\-_]{4,20}).*?([\d,]+\.\d{2})')

    for line in raw_text.split('\n'):
        line_clean = line.strip().upper()
        if any(keyword in line_clean for keyword in ['STATEMENT', 'TOTAL', 'PAGE', 'DATE', 'BALANCE']):
            continue

        match = pattern.search(line_clean)
        if match:
            doc_ref = match.group(1).strip()
            amount_str = match.group(2).replace(',', '')
            try:
                amt = float(amount_str)
                if amt > 0:
                    parsed_records.append({'doc_ref': doc_ref, 'soa_amount': amt})
            except ValueError:
                continue

    df_result = pd.DataFrame(parsed_records)
    if not df_result.empty:
        df_result.drop_duplicates(subset=['doc_ref'], keep='first', inplace=True)
    return df_result