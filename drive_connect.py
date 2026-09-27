"""One-time Google Drive sign-in for the thermal statement QR copies (2026-09-27). Run by drive_connect.bat.

Needs credentials.json (an OAuth "Desktop app" client from Google Cloud) in the drive folder (normally C:\\AP_Data\\drive). Opens the
browser; you sign in with the Google account that should hold the statement copies and click Allow. The app only gets access to the
files it creates itself (drive.file), nothing else in that Drive. Safe to run again (e.g. to switch account or after a revoked sign-in).
"""
import os
import sys

import db_location
from engines import drive_service

STEPS = r"""
credentials.json is not there yet. One-time setup (about 10 minutes, in the browser):

 1. Go to https://console.cloud.google.com and sign in with the Google account that should hold the statement copies.
 2. Create a project (top bar > project picker > New project), name it e.g. "Slicks AP", and select it.
 3. APIs & Services > Library > search "Google Drive API" > Enable.
 4. APIs & Services > OAuth consent screen (Google Auth Platform): app name "Slicks AP", your email as support/contact
    email, audience "External", and under Test users add the same Google account. Save.
    Then under Audience click "Publish app" (status "In production"). Otherwise Google signs the app out every 7 days.
    No Google review is needed: the app only asks for access to the files it creates itself.
 5. APIs & Services > Credentials > Create credentials > OAuth client ID > Application type "Desktop app" > Create.
 6. Click "Download JSON", then rename the downloaded file to credentials.json and move it into:
       {folder}
 7. Double-click drive_connect.bat again.

Keep credentials.json and token.json private: they are never put in the project folder or in git.
"""


def main() -> int:
    folder = db_location.drive_dir()
    os.makedirs(folder, exist_ok=True)
    if not os.path.exists(drive_service.credentials_path()):
        print(STEPS.format(folder=folder))
        return 1
    print("A browser window will open. Sign in with the Google account for the statement copies and click Allow.")
    try:
        email = drive_service.connect_interactive()
    except Exception as e:
        print(f"\nSign-in did not finish: {e}")
        return 1
    print(f"\nConnected to Google Drive as {email}.")
    print(f"Sign-in saved in {drive_service.token_path()} (private -- never in the project folder or git).")
    print("The app creates its own folder for the statement copies the first time it prints a slip with a QR code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
