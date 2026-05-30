import os
import sys
from datetime import datetime
from pymongo import MongoClient
from dotenv import load_dotenv

# Add project root to path to allow direct script execution
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def reset_user_submission_limits():
    """
    Connects to MongoDB and resets the submission counts for a specific user.
    """
    # Load environment variables from .env file
    load_dotenv()

    # --- Database Connection ---
    connection_string = os.getenv("MONGODB_URI", os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    database_name = os.getenv("MONGO_AIFOREN_DB_NAME", os.getenv("MONGODB_DB_NAME", "aiforen_db"))

    if not connection_string or not database_name:
        print("❌ Error: MONGODB_URI and MONGO_AIFOREN_DB_NAME must be set in your .env file.")
        return

    try:
        client = MongoClient(connection_string)
        db = client[database_name]
        print(f"✅ Connected to MongoDB database: '{database_name}'")
    except Exception as e:
        print(f"❌ Failed to connect to MongoDB: {e}")
        return

    # --- Collections ---
    stats_collection = db.user_writing_stats

    # --- Reset Logic ---
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    month_str = datetime.utcnow().strftime("%Y-%m")

    # This filter will apply the reset to all users who have a stats entry.
    # It's generally safe for a development environment to reset everyone.
    filter_query = {} 

    update_query = {
        "$set": {
            f"daily_submissions.{today_str}": 0,
            f"monthly_submissions.{month_str}": 0
        }
    }

    # Use update_many to reset all matching documents
    result = stats_collection.update_many(filter_query, update_query)

    if result.matched_count > 0:
        print(f"\n✅ Found {result.matched_count} user stat entries.")
        if result.modified_count > 0:
            print(f"🎉 Successfully reset submission limits for {result.modified_count} user(s).")
        else:
            print("✅ No users required a limit reset.")
    else:
        print("\n❌ No user stats entries found in the database. No changes made.")

if __name__ == "__main__":
    reset_user_submission_limits() 