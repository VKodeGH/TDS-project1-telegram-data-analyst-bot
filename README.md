# TDS-project1-telegram-data-analyst-bot

On `/start`, the bot collects the user's name, age, and profile photo, then requests their location and phone number using Telegram's share buttons. Completed profiles are appended to a configured Google Sheet. The local CSV and downloaded photos are only fallback/local copies; Render's free filesystem is temporary.

## Google Sheets setup

1. Create a Google Cloud project and enable the Google Sheets API.
2. Create a service account and download its JSON key.
3. Create a Google Sheet and share it with the service account email as Editor. Copy the spreadsheet ID from its URL.
4. Add these Render environment variables (paste the complete JSON key as one value):

```text
GOOGLE_SHEET_ID=your_spreadsheet_id
GOOGLE_WORKSHEET_NAME=User Profiles
GOOGLE_SERVICE_ACCOUNT_JSON=the_complete_service_account_json
```

The bot creates the `User Profiles` worksheet and its header row automatically. View the saved user records in that Google Sheet. The photo itself is not uploaded to Sheets; the row stores its Telegram `photo_file_id` and local photo path.