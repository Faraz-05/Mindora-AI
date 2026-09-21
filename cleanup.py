# import sqlite3

# DB_PATH = "chatbot_1.db"
# KEEP_ID = "17cb83c6-4caf-4d22-b55e-f000af0a0909"

# conn = sqlite3.connect(DB_PATH)

# try:
#     cursor = conn.cursor()

#     queries = {
#         "messages": """
#             SELECT COUNT(*)
#             FROM messages
#             WHERE conversation_id != ?
#         """,
#         "checkpoints": """
#             SELECT COUNT(*)
#             FROM checkpoints
#             WHERE thread_id != ?
#         """,
#         "conversations": """
#             SELECT COUNT(*)
#             FROM conversations
#             WHERE conversation_id != ?
#         """,
#     }

#     for table, query in queries.items():
#         cursor.execute(query, (KEEP_ID,))
#         count = cursor.fetchone()[0]
#         print(f"{table}: {count} records would be deleted")

# finally:
#     conn.close()


import sqlite3

DB_PATH = "chatbot_1.db"
KEEP_ID = "17cb83c6-4caf-4d22-b55e-f000af0a0909"

conn = sqlite3.connect(DB_PATH)

try:
    cursor = conn.cursor()

    # Show what will be deleted
    queries = {
        "messages": """
            SELECT COUNT(*)
            FROM messages
            WHERE conversation_id != ?
        """,
        "checkpoints": """
            SELECT COUNT(*)
            FROM checkpoints
            WHERE thread_id != ?
        """,
        "conversations": """
            SELECT COUNT(*)
            FROM conversations
            WHERE conversation_id != ?
        """,
    }

    print("Records to be deleted:")
    print("-" * 40)

    for table, query in queries.items():
        cursor.execute(query, (KEEP_ID,))
        count = cursor.fetchone()[0]
        print(f"{table}: {count}")

    # Ask for confirmation
    confirm = input("\nDelete these records? Type 'DELETE' to continue: ")

    if confirm != "DELETE":
        print("Cancelled. Nothing was deleted.")
    else:
        # Delete messages first
        cursor.execute(
            """
            DELETE FROM messages
            WHERE conversation_id != ?
            """,
            (KEEP_ID,),
        )
        print(f"Deleted {cursor.rowcount} messages")

        # Delete checkpoints
        cursor.execute(
            """
            DELETE FROM checkpoints
            WHERE thread_id != ?
            """,
            (KEEP_ID,),
        )
        print(f"Deleted {cursor.rowcount} checkpoints")

        # Delete conversations
        cursor.execute(
            """
            DELETE FROM conversations
            WHERE conversation_id != ?
            """,
            (KEEP_ID,),
        )
        print(f"Deleted {cursor.rowcount} conversations")

        # Commit everything
        conn.commit()

        print("\nCleanup completed successfully.")
        print(f"Kept conversation: {KEEP_ID}")

except Exception as e:
    conn.rollback()
    print(f"\nError: {e}")
    print("All changes have been rolled back.")

finally:
    conn.close()