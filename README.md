# TDS-project1-telegram-data-analyst-bot

On `/start`, the bot collects the user's name, age, and profile photo, then requests their location and phone number using Telegram's share buttons. Completed profiles are appended to `user_profiles.csv`; downloaded profile photos are stored in `user_photos/` and referenced by path in the CSV.