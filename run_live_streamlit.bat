@echo off
REM Live instance -- no AP_DB_PATH override, so app.py uses its default
REM (data\ap_disbursement.db). Port 8501, matching .claude\launch.json's
REM "ap-disbursement" config.
"C:\Users\Rhodora\AppData\Local\Programs\Python\Python312\python.exe" -m streamlit run "H:\My Drive\Claude9OS\Project_Checke\app.py" --server.headless true --server.port 8501
