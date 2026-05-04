import socket
import getpass

HOST = "127.0.0.1"
PORT = 5000


def send_and_receive(client_socket, message):
    client_socket.send(message.encode())
    return client_socket.recv(4096).decode()


def extract_login_data(response):
    username = None
    session_token = None

    parts = response.split("|")
    if len(parts) >= 3 and parts[0] == "SUCCESS":
        session_token = parts[-1]
        if "_" in session_token:
            username = session_token.split("_")[0]

    return username, session_token


def get_role_from_username(username):
    if username == "manager":
        return "MANAGER"
    if username == "chef":
        return "CHEF"
    return "SERVER"


def extract_order_data_from_create_response(response):
    order_id = None
    order_type = None
    table_number = None
    guests = None
    customer_name = ""

    parts = response.split("|")
    for part in parts:
        if part.startswith("OrderID:"):
            order_id = int(part.split(":", 1)[1])
        elif part.startswith("Type:"):
            order_type = part.split(":", 1)[1]
        elif part.startswith("Table:"):
            table_number = int(part.split(":", 1)[1])
        elif part.startswith("Guests:"):
            guests = int(part.split(":", 1)[1])
        elif part.startswith("Name:"):
            customer_name = part.split(":", 1)[1]

    return order_id, order_type, table_number, guests, customer_name


def print_menu_response(response):
    parts = response.split("|")[1:]

    print("\n====== MENU ======\n")
    for item in parts:
        if item.isupper():
            print(f"{item}:")
        else:
            name, price = item.split(":")
            print(f"  {name} - ${price}")
    print()


def print_tables_response(response):
    if response == "TABLES|NONE":
        print("\nNo active dine-in tables.\n")
        return {}

    parts = response.split("|")[1:]
    table_map = {}

    print("\n====== ACTIVE DINE-IN TABLES ======\n")

    for part in parts:
        fields = part.split(",")

        table_number = None
        order_id = None
        guests = None
        status = ""
        remaining = ""

        for field in fields:
            if field.startswith("Table:"):
                table_number = int(field.split(":", 1)[1])
            elif field.startswith("OrderID:"):
                order_id = int(field.split(":", 1)[1])
            elif field.startswith("Guests:"):
                guests = int(field.split(":", 1)[1])
            elif field.startswith("Status:"):
                status = field.split(":", 1)[1]
            elif field.startswith("Remaining:"):
                remaining = field.split(":", 1)[1]

        if table_number is not None and order_id is not None and guests is not None:
            table_map[table_number] = {
                "order_id": order_id,
                "guests": guests,
                "status": status,
                "remaining": remaining
            }

            extra = []
            if status:
                extra.append(f"Status: {status}")
            if remaining != "":
                extra.append(f"Remaining: ${remaining}")

            suffix = ""
            if extra:
                suffix = " | " + " | ".join(extra)

            print(
                f"  Table {table_number} (OrderID: {order_id}, Guests: {guests}){suffix}"
            )

    print()
    return table_map


def choose_table(table_map):
    if not table_map:
        print("No active dine-in tables.\n")
        return None

    while True:
        table_input = input("Choose a table number from the list above: ").strip()

        try:
            table_number = int(table_input)
        except ValueError:
            print("Please enter a valid number.")
            continue

        if table_number in table_map:
            return table_number

        print("That table is not in the active options.")


def choose_seat(guest_count):
    print("\nAvailable seat options:")
    for seat in range(1, guest_count + 1):
        print(f"  Seat {seat}")

    while True:
        seat_input = input("Choose a seat number: ").strip()

        try:
            seat_number = int(seat_input)
        except ValueError:
            print("Please enter a valid seat number.")
            continue

        if 1 <= seat_number <= guest_count:
            return str(seat_number)

        print(f"Please choose a seat between 1 and {guest_count}.")


def print_orders_response(response):
    if response == "ORDERS|NONE":
        print("\nNo orders found.\n")
        return

    parts = response.split("|")[1:]

    print("\n====== ORDERS ======\n")
    for part in parts:
        fields = part.split(",")

        info = {}
        for field in fields:
            if ":" in field:
                key, value = field.split(":", 1)
                info[key] = value

        order_id = info.get("OrderID", "")
        order_type = info.get("Type", "")
        status = info.get("Status", "")
        total = info.get("Total", "0.00")
        paid = info.get("Paid", "0.00")
        remaining = info.get("Remaining", "0.00")

        if order_type == "DINE_IN":
            table_number = info.get("Table", "")
            guests = info.get("Guests", "")
            print(
                f"Order {order_id} | Type: {order_type} | Table: {table_number} | "
                f"Guests: {guests} | Status: {status} | "
                f"Total: ${total} | Paid: ${paid} | Remaining: ${remaining}"
            )
        else:
            customer_name = info.get("Name", "")
            print(
                f"Order {order_id} | Type: {order_type} | Name: {customer_name} | "
                f"Status: {status} | Total: ${total} | Paid: ${paid} | Remaining: ${remaining}"
            )

    print()


def print_kitchen_queue_response(response, title="KITCHEN QUEUE"):
    if response == "QUEUE|NONE":
        print(f"\nNo items found for {title.lower()}.\n")
        return

    parts = response.split("|")[1:]

    print(f"\n====== {title} ======\n")
    for part in parts:
        fields = part.split(",")

        info = {}
        for field in fields:
            if ":" in field:
                key, value = field.split(":", 1)
                info[key] = value

        kitchen_item_id = info.get("KitchenItemID", "")
        order_id = info.get("OrderID", "")
        table_number = info.get("Table", "")
        seat_number = info.get("Seat", "")
        customer_name = info.get("Name", "")
        item_name = info.get("Item", "")
        qty = info.get("Qty", "")
        timestamp = info.get("Time", "")
        status = info.get("Status", "")

        if table_number == "0":
            if customer_name:
                location = f"TAKE_OUT ({customer_name})"
            else:
                location = "TAKE_OUT"
        else:
            location = f"Table {table_number}"

        print(
            f"KitchenItem {kitchen_item_id} | "
            f"Order {order_id} | "
            f"{location} | "
            f"Seat {seat_number} | "
            f"{item_name} x{qty} | "
            f"{timestamp} | "
            f"{status}"
        )

    print()


def print_bill_response(response):
    if response.startswith("ERROR|"):
        print(f"\nServer response: {response}\n")
        return

    parts = response.split("|")

    print("\n====== BILL ======\n")

    for part in parts[1:]:
        if part.startswith("OrderID:"):
            print(f"Order ID: {part.split(':', 1)[1]}")
        elif part.startswith("Type:"):
            print(f"Type: {part.split(':', 1)[1]}")
        elif part.startswith("Table:"):
            print(f"Table: {part.split(':', 1)[1]}")
        elif part.startswith("Name:"):
            print(f"Customer Name: {part.split(':', 1)[1]}")
        elif part.startswith("Total:"):
            print(f"Total: ${part.split(':', 1)[1]}")
        elif part.startswith("Paid:"):
            print(f"Paid: ${part.split(':', 1)[1]}")
        elif part.startswith("Remaining:"):
            print(f"Remaining: ${part.split(':', 1)[1]}")
        elif part.startswith("Status:"):
            print(f"Status: {part.split(':', 1)[1]}")
        elif part.startswith("Seat:"):
            seat_data = part.split(":", 1)[1]
            seat_parts = seat_data.split(",")
            if len(seat_parts) == 4:
                print(
                    f"Seat {seat_parts[0]} | "
                    f"Total: ${seat_parts[1]} | "
                    f"Paid: ${seat_parts[2]} | "
                    f"Remaining: ${seat_parts[3]}"
                )
    print()


def print_view_order_response(response):
    if response.startswith("ERROR|"):
        print(f"\nServer response: {response}\n")
        return

    parts = response.split("|")

    if not parts or parts[0] != "ORDERVIEW":
        print(f"\nServer response: {response}\n")
        return

    print("\n====== ORDER DETAILS ======\n")

    seat_totals = []
    item_lines = []

    for part in parts[1:]:
        if part.startswith("OrderID:"):
            print(f"Order ID: {part.split(':', 1)[1]}")
        elif part.startswith("Type:"):
            print(f"Type: {part.split(':', 1)[1]}")
        elif part.startswith("Status:"):
            print(f"Status: {part.split(':', 1)[1]}")
        elif part.startswith("Table:"):
            print(f"Table: {part.split(':', 1)[1]}")
        elif part.startswith("Guests:"):
            print(f"Guests: {part.split(':', 1)[1]}")
        elif part.startswith("Name:"):
            print(f"Customer Name: {part.split(':', 1)[1]}")
        elif part.startswith("Total:"):
            print(f"Total: ${part.split(':', 1)[1]}")
        elif part.startswith("Paid:"):
            print(f"Paid: ${part.split(':', 1)[1]}")
        elif part.startswith("Remaining:"):
            print(f"Remaining: ${part.split(':', 1)[1]}")
        elif part.startswith("SEATTOTAL:"):
            seat_totals.append(part.split(":", 1)[1])
        elif part.startswith("ITEM:"):
            item_lines.append(part.split(":", 1)[1])

    if seat_totals:
        print("\nSeat Totals:")
        for seat in seat_totals:
            seat_parts = seat.split(",")
            if len(seat_parts) == 4:
                print(
                    f"  Seat {seat_parts[0]} | "
                    f"Total: ${seat_parts[1]} | "
                    f"Paid: ${seat_parts[2]} | "
                    f"Remaining: ${seat_parts[3]}"
                )

    if item_lines:
        print("\nItems:")
        for line in item_lines:
            item_parts = line.split(",")
            if len(item_parts) == 6:
                seat_label = item_parts[0]
                item_name = item_parts[1]
                qty = item_parts[2]
                unit_price = item_parts[3]
                line_total = item_parts[4]
                kitchen_status = item_parts[5]

                print(
                    f"  {seat_label} | {item_name} x{qty} | "
                    f"${unit_price} each | Line Total: ${line_total} | "
                    f"Kitchen: {kitchen_status}"
                )
    print()


def choose_order_id_any():
    value = input("Order ID: ").strip()
    try:
        return int(value)
    except ValueError:
        print("Order ID must be a number.\n")
        return None


def choose_takeout_order_from_list(client_socket, session_token):
    response = send_and_receive(client_socket, f"LISTORDERS|{session_token}")

    if response == "ORDERS|NONE":
        print("\nNo orders found.\n")
        return None

    if not response.startswith("ORDERS|"):
        print(f"\nServer response: {response}\n")
        return None

    parts = response.split("|")[1:]
    takeout_ids = []

    print("\n====== TAKE-OUT ORDERS ======\n")
    for part in parts:
        fields = part.split(",")
        info = {}
        for field in fields:
            if ":" in field:
                key, value = field.split(":", 1)
                info[key] = value

        if info.get("Type") == "TAKE_OUT" and info.get("Status") != "PAID":
            order_id = int(info["OrderID"])
            takeout_ids.append(order_id)
            print(
                f"Order {order_id} | Name: {info.get('Name', '')} | "
                f"Status: {info.get('Status', '')} | Remaining: ${info.get('Remaining', '0.00')}"
            )

    if not takeout_ids:
        print("\nNo active take-out orders found.\n")
        return None

    while True:
        value = input("\nChoose a take-out order ID: ").strip()
        try:
            order_id = int(value)
        except ValueError:
            print("Please enter a valid order ID.")
            continue

        if order_id in takeout_ids:
            return order_id

        print("That order ID is not in the active options.")


def start_client():
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    print("Connecting to restaurant server...")
    client_socket.connect((HOST, PORT))
    print("Connected.\n")

    session_token = None
    current_order_id = None
    current_takeout_order_id = None
    username = None
    role = None

    while True:
        if session_token is None:
            print("Available commands:")
            print("login")
            print("exit")

            cmd = input("\nEnter command: ").strip().lower()

            if cmd == "login":
                username_input = input("Username: ").strip()
                password = getpass.getpass("Password: ")

                response = send_and_receive(client_socket, f"LOGIN|{username_input}|{password}")
                print(f"\nServer response: {response}")

                if "Login successful" in response:
                    username, session_token = extract_login_data(response)
                    role = get_role_from_username(username)

            elif cmd == "exit":
                response = send_and_receive(client_socket, "exit")
                print(f"\nServer response: {response}")
                break

            else:
                print("Invalid command.\n")

        else:
            if role == "CHEF":
                print("\nAvailable commands:")
                print("view pending")
                print("view all")
                print("complete")
                print("list")
                print("logout")
                print("exit")

                cmd = input("\nEnter command: ").strip().lower()

                if cmd == "view pending":
                    response = send_and_receive(client_socket, f"VIEWQUEUE|{session_token}|PENDING")
                    if response.startswith("QUEUE"):
                        print_kitchen_queue_response(response, "PENDING KITCHEN ITEMS")
                    else:
                        print(f"\nServer response: {response}")

                elif cmd == "view all":
                    response = send_and_receive(client_socket, f"VIEWQUEUE|{session_token}|ALL")
                    if response.startswith("QUEUE"):
                        print_kitchen_queue_response(response, "ALL KITCHEN ITEMS")
                    else:
                        print(f"\nServer response: {response}")

                elif cmd == "complete":
                    response = send_and_receive(client_socket, f"VIEWQUEUE|{session_token}|PENDING")

                    if response == "QUEUE|NONE":
                        print("\nNo pending kitchen items.\n")
                        continue

                    if not response.startswith("QUEUE"):
                        print(f"\nServer response: {response}")
                        continue

                    print_kitchen_queue_response(response, "PENDING KITCHEN ITEMS")

                    kitchen_item_id = input("Enter kitchen item ID to complete: ").strip()

                    complete_response = send_and_receive(
                        client_socket,
                        f"COMPLETEITEM|{session_token}|{kitchen_item_id}"
                    )
                    print(f"\nServer response: {complete_response}")

                elif cmd == "list":
                    response = send_and_receive(client_socket, f"LISTORDERS|{session_token}")
                    if response.startswith("ORDERS"):
                        print_orders_response(response)
                    else:
                        print(f"\nServer response: {response}")

                elif cmd == "logout":
                    response = send_and_receive(client_socket, f"LOGOUT|{session_token}")
                    print(f"\nServer response: {response}")

                    if "Logged out" in response:
                        session_token = None
                        current_order_id = None
                        current_takeout_order_id = None
                        username = None
                        role = None

                elif cmd == "exit":
                    response = send_and_receive(client_socket, "exit")
                    print(f"\nServer response: {response}")
                    break

                else:
                    print("Invalid command.\n")

            else:
                print("\nAvailable commands:")
                print("menu")
                print("create")
                print("add")
                print("remove")
                print("view")
                print("cashout")
                print("list")
                if role == "MANAGER":
                    print("updateprice")
                    print("view pending")
                    print("view all")
                print("logout")
                print("exit")

                cmd = input("\nEnter command: ").strip().lower()

                if cmd == "menu":
                    response = send_and_receive(client_socket, f"GETMENU|{session_token}")

                    if response.startswith("MENU"):
                        print_menu_response(response)
                    else:
                        print(f"\nServer response: {response}")

                elif cmd == "create":
                    order_type = input("Order type (DINE_IN or TAKE_OUT): ").strip().upper()

                    if order_type == "DINE_IN":
                        table_number = input("Table number: ").strip()
                        guests = input("Number of guests (1-4): ").strip()
                        response = send_and_receive(
                            client_socket,
                            f"CREATEORDER|{session_token}|{order_type}|{table_number}|{guests}|"
                        )

                    elif order_type == "TAKE_OUT":
                        customer_name = input("Customer name: ").strip()
                        response = send_and_receive(
                            client_socket,
                            f"CREATEORDER|{session_token}|{order_type}|0|0|{customer_name}"
                        )

                    else:
                        print("Invalid order type.\n")
                        continue

                    print(f"\nServer response: {response}")

                    if "Order created" in response:
                        order_id, created_type, _, _, _ = extract_order_data_from_create_response(response)
                        current_order_id = order_id

                        if created_type == "TAKE_OUT":
                            current_takeout_order_id = order_id

                elif cmd == "add":
                    print("\nAdd item to:")
                    print("1. Dine-in table")
                    print("2. Take-out order")

                    add_choice = input("Choose option (1 or 2): ").strip()

                    if add_choice == "1":
                        response = send_and_receive(client_socket, f"LISTTABLES|{session_token}")

                        if response == "TABLES|NONE":
                            print("\nNo active dine-in tables available. Create a dine-in order first.\n")
                            continue

                        if not response.startswith("TABLES"):
                            print(f"\nServer response: {response}")
                            continue

                        table_map = print_tables_response(response)
                        chosen_table = choose_table(table_map)

                        if chosen_table is None:
                            continue

                        order_info = table_map[chosen_table]
                        chosen_order_id = order_info["order_id"]
                        guest_count = order_info["guests"]

                        item_name = input("Item name: ").strip()
                        qty = input("Quantity: ").strip()
                        seat = choose_seat(guest_count)

                        add_response = send_and_receive(
                            client_socket,
                            f"ADDITEM|{session_token}|{chosen_order_id}|{item_name}|{qty}|{seat}"
                        )
                        print(f"\nServer response: {add_response}")
                        current_order_id = chosen_order_id

                    elif add_choice == "2":
                        chosen_order_id = choose_takeout_order_from_list(client_socket, session_token)
                        if chosen_order_id is None:
                            continue

                        item_name = input("Item name: ").strip()
                        qty = input("Quantity: ").strip()

                        add_response = send_and_receive(
                            client_socket,
                            f"ADDITEM|{session_token}|{chosen_order_id}|{item_name}|{qty}|0"
                        )
                        print(f"\nServer response: {add_response}")
                        current_order_id = chosen_order_id
                        current_takeout_order_id = chosen_order_id

                    else:
                        print("Invalid option.\n")

                elif cmd == "remove":
                    print("\nRemove item from:")
                    print("1. Dine-in table")
                    print("2. Take-out order")

                    remove_choice = input("Choose option (1 or 2): ").strip()

                    if remove_choice == "1":
                        response = send_and_receive(client_socket, f"LISTTABLES|{session_token}")

                        if response == "TABLES|NONE":
                            print("\nNo active dine-in tables available.\n")
                            continue

                        if not response.startswith("TABLES"):
                            print(f"\nServer response: {response}")
                            continue

                        table_map = print_tables_response(response)
                        chosen_table = choose_table(table_map)

                        if chosen_table is None:
                            continue

                        order_info = table_map[chosen_table]
                        chosen_order_id = order_info["order_id"]
                        guest_count = order_info["guests"]

                        view_response = send_and_receive(
                            client_socket,
                            f"VIEWORDER|{session_token}|{chosen_order_id}"
                        )
                        print_view_order_response(view_response)

                        item_name = input("Item name: ").strip()
                        qty = input("Quantity to remove: ").strip()
                        seat = choose_seat(guest_count)

                        remove_response = send_and_receive(
                            client_socket,
                            f"REMOVEITEM|{session_token}|{chosen_order_id}|{item_name}|{qty}|{seat}"
                        )
                        print(f"\nServer response: {remove_response}")

                    elif remove_choice == "2":
                        chosen_order_id = choose_takeout_order_from_list(client_socket, session_token)
                        if chosen_order_id is None:
                            continue

                        view_response = send_and_receive(
                            client_socket,
                            f"VIEWORDER|{session_token}|{chosen_order_id}"
                        )
                        print_view_order_response(view_response)

                        item_name = input("Item name: ").strip()
                        qty = input("Quantity to remove: ").strip()

                        remove_response = send_and_receive(
                            client_socket,
                            f"REMOVEITEM|{session_token}|{chosen_order_id}|{item_name}|{qty}|0"
                        )
                        print(f"\nServer response: {remove_response}")

                    else:
                        print("Invalid option.\n")

                elif cmd == "view":
                    print("\nView options:")
                    print("1. Dine-in table")
                    print("2. Take-out order")
                    print("3. Any order by ID")

                    view_choice = input("Choose option (1, 2, or 3): ").strip()

                    if view_choice == "1":
                        response = send_and_receive(client_socket, f"LISTTABLES|{session_token}")

                        if response == "TABLES|NONE":
                            print("\nNo active dine-in tables.\n")
                            continue

                        if not response.startswith("TABLES"):
                            print(f"\nServer response: {response}")
                            continue

                        table_map = print_tables_response(response)
                        chosen_table = choose_table(table_map)
                        if chosen_table is None:
                            continue

                        order_id = table_map[chosen_table]["order_id"]

                    elif view_choice == "2":
                        order_id = choose_takeout_order_from_list(client_socket, session_token)
                        if order_id is None:
                            continue

                    elif view_choice == "3":
                        order_id = choose_order_id_any()
                        if order_id is None:
                            continue

                    else:
                        print("Invalid option.\n")
                        continue

                    response = send_and_receive(client_socket, f"VIEWORDER|{session_token}|{order_id}")
                    print_view_order_response(response)

                elif cmd == "cashout":
                    print("\nCashout options:")
                    print("1. Dine-in table")
                    print("2. Take-out order")

                    cashout_choice = input("Choose option (1 or 2): ").strip()

                    if cashout_choice == "1":
                        response = send_and_receive(client_socket, f"LISTTABLES|{session_token}")

                        if response == "TABLES|NONE":
                            print("\nNo active dine-in tables.\n")
                            continue

                        if not response.startswith("TABLES"):
                            print(f"\nServer response: {response}")
                            continue

                        table_map = print_tables_response(response)
                        chosen_table = choose_table(table_map)
                        if chosen_table is None:
                            continue

                        order_info = table_map[chosen_table]
                        order_id = order_info["order_id"]
                        guest_count = order_info["guests"]

                        bill_response = send_and_receive(client_socket, f"GETBILL|{session_token}|{order_id}")
                        print_bill_response(bill_response)

                        print("\nPay by:")
                        print("1. Seat")
                        print("2. Table")

                        pay_choice = input("Choose option (1 or 2): ").strip()

                        if pay_choice == "1":
                            seat_number = choose_seat(guest_count)
                            response = send_and_receive(
                                client_socket,
                                f"CASHOUT|{session_token}|{order_id}|SEAT|{seat_number}"
                            )
                        elif pay_choice == "2":
                            response = send_and_receive(
                                client_socket,
                                f"CASHOUT|{session_token}|{order_id}|TABLE|0"
                            )
                        else:
                            print("Invalid option.\n")
                            continue

                        print(f"\nServer response: {response}")

                    elif cashout_choice == "2":
                        order_id = choose_takeout_order_from_list(client_socket, session_token)
                        if order_id is None:
                            continue

                        bill_response = send_and_receive(client_socket, f"GETBILL|{session_token}|{order_id}")
                        print_bill_response(bill_response)

                        response = send_and_receive(
                            client_socket,
                            f"CASHOUT|{session_token}|{order_id}|TAKEOUT|0"
                        )
                        print(f"\nServer response: {response}")

                    else:
                        print("Invalid option.\n")

                elif cmd == "list":
                    response = send_and_receive(client_socket, f"LISTORDERS|{session_token}")

                    if response.startswith("ORDERS"):
                        print_orders_response(response)
                    else:
                        print(f"\nServer response: {response}")

                elif cmd == "updateprice" and role == "MANAGER":
                    item_name = input("Item name: ").strip()
                    new_price = input("New price: ").strip()

                    response = send_and_receive(
                        client_socket,
                        f"UPDATEPRICE|{session_token}|{item_name}|{new_price}"
                    )
                    print(f"\nServer response: {response}")

                elif cmd == "view pending" and role == "MANAGER":
                    response = send_and_receive(client_socket, f"VIEWQUEUE|{session_token}|PENDING")
                    if response.startswith("QUEUE"):
                        print_kitchen_queue_response(response, "PENDING KITCHEN ITEMS")
                    else:
                        print(f"\nServer response: {response}")

                elif cmd == "view all" and role == "MANAGER":
                    response = send_and_receive(client_socket, f"VIEWQUEUE|{session_token}|ALL")
                    if response.startswith("QUEUE"):
                        print_kitchen_queue_response(response, "ALL KITCHEN ITEMS")
                    else:
                        print(f"\nServer response: {response}")

                elif cmd == "logout":
                    response = send_and_receive(client_socket, f"LOGOUT|{session_token}")
                    print(f"\nServer response: {response}")

                    if "Logged out" in response:
                        session_token = None
                        current_order_id = None
                        current_takeout_order_id = None
                        username = None
                        role = None

                elif cmd == "exit":
                    response = send_and_receive(client_socket, "exit")
                    print(f"\nServer response: {response}")
                    break

                else:
                    print("Invalid command.\n")

    client_socket.close()
    print("Connection closed")


if __name__ == "__main__":
    start_client()
