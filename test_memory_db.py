import json
import uuid

from langgraph_backend import (
    conn,
    init_memory_db,
    insert_memory,
    get_memory,
    get_all_memory,
)


def print_section(title):
    print(f"\n--- {title} ---")


def test_insert_and_get():
    init_memory_db()

    namespace = ("user", "u1", "details")
    key = str(uuid.uuid4())
    value = {"data":"user name is Maaiz"}

    insert_memory(namespace, key, value)

    print_section("GET MEMORY")
    print(get_memory(namespace, key))

    print_section("GET ALL MEMORY")
    print(get_all_memory(namespace))


def clear_memory_table():
    cursor = conn.cursor()
    cursor.execute("DELETE FROM memory")
    conn.commit()
    print_section("CLEARED MEMORY")
    print("Memory table emptied.")


if __name__ == "__main__":
    test_insert_and_get()
    clear_memory_table()
