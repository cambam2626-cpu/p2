import socket
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

HOST = "127.0.0.1"
PORT = 5000

sessions = {}
orders = {}
next_order_id = 1000

kitchen_queue = []
next_kitchen_item_id = 1

MENU_ITEMS = {
    "STARTERS": [
        ("Fried Pickles", 6.99),
        ("Stuffed Mushrooms", 7.99),
        ("Wings", 9.99)
    ],
    "MAINS": [
        ("Buffalo Chicken Sandwich", 11.99),
        ("Cheeseburger", 12.99),
        ("Chili Cheese Dog", 10.99),
        ("Cuban Sandwich", 13.99),
        ("Full Rack Ribs", 18.99)
    ],
    "DESSERTS": [
        ("Banana Split", 7.99),
        ("Chocolate Cake", 6.99),
        ("Honey Ice Cream", 5.99)
    ],
    "DRINKS": [
        ("Coke", 2.99),
        ("Fanta", 2.99),
        ("Milkshake", 4.99),
        ("Sprite", 2.99),
        ("Water", 0.00)
    ]
}

USERS = {
    "manager": "pw",
    "server1": "pw",
    "server2": "pw",
    "chef": "pw"
}


def safe_log_message(message):
    parts = message.split("|")
    if parts[0] == "LOGIN" and len(parts) >= 3:
        return f"LOGIN|{parts[1]}|***"
    return message


def money(value):
    return Decimal(str(value))


def round_money(value):
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def get_role_from_username(username):
    if username == "manager":
        return "MANAGER"
    if username == "chef":
        return "CHEF"
    return "SERVER"


def is_authorized(session_token):
    return session_token in sessions


def require_role(session_token, allowed_roles):
    if session_token not in sessions:
        return None, "Unauthorized"

    username = sessions[session_token]
    role = get_role_from_username(username)

    if role not in allowed_roles:
        return None, "Forbidden"

    return {"username": username, "role": role}, None


def normalize_item_name(item_name):
    for category, items in MENU_ITEMS.items():
        for name, _ in items:
            if name.lower() == item_name.lower().strip():
                return name
    return None


def find_menu_item(item_name):
    for category, items in MENU_ITEMS.items():
        for name, price in items:
            if name.lower() == item_name.lower():
                return name, price, category
    return None


def current_timestamp_string():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_kitchen_item(item_name):
    item = find_menu_item(item_name)
    if item is None:
        return False

    _, _, category = item
    return category != "DRINKS"


def get_active_dine_in_order_for_table(table_number):
    for order_id, order in orders.items():
        if (
            order["type"] == "DINE_IN"
            and order["table_number"] == table_number
            and order["status"] != "PAID"
        ):
            return order_id, order
    return None, None


def get_item_price(item_name):
    item = find_menu_item(item_name)
    if item is None:
        return money("0.00")
    _, price, _ = item
    return money(price)


def get_takeout_total(order):
    total = money("0.00")
    for item_name, qty in order["items"].items():
        total += get_item_price(item_name) * qty
    return total


def get_seat_total(order, seat_number):
    seat_total = money("0.00")
    seat_items = order["seat_items"].get(seat_number, {})
    for item_name, qty in seat_items.items():
        seat_total += get_item_price(item_name) * qty
    return seat_total


def get_order_total(order):
    if order["type"] == "TAKE_OUT":
        return get_takeout_total(order)

    total = money("0.00")
    for seat_number in range(1, order["guests"] + 1):
        total += get_seat_total(order, seat_number)
    return total


def recompute_order_status(order_id):
    order = orders[order_id]
    total = get_order_total(order)
    paid = money(order.get("paid_amount", 0.0))

    if total > money("0.00") and paid >= total:
        order["status"] = "PAID"
        return

    if paid > money("0.00"):
        order["status"] = "PARTIALLY_PAID"
        return

    for item in kitchen_queue:
        if item["order_id"] == order_id and item["status"] == "PENDING":
            order["status"] = "PLACED"
            return

    order["status"] = "READY"


def add_to_kitchen_queue(order_id, order, item_name, quantity, seat_number):
    global next_kitchen_item_id

    if not is_kitchen_item(item_name):
        return

    kitchen_queue.append({
        "kitchen_item_id": next_kitchen_item_id,
        "order_id": order_id,
        "table_number": order["table_number"],
        "seat_number": seat_number,
        "customer_name": order.get("customer_name", ""),
        "item_name": item_name,
        "quantity": quantity,
        "timestamp": current_timestamp_string(),
        "created_at": time.time(),
        "status": "PENDING"
    })

    next_kitchen_item_id += 1


def remove_from_kitchen_queue(order_id, item_name, quantity, seat_number):
    remaining_to_remove = quantity

    pending_items = sorted(
        [
            item for item in kitchen_queue
            if item["order_id"] == order_id
            and item["item_name"].lower() == item_name.lower()
            and item["seat_number"] == seat_number
            and item["status"] == "PENDING"
        ],
        key=lambda x: (x["created_at"], x["kitchen_item_id"])
    )

    for item in pending_items:
        if remaining_to_remove <= 0:
            break

        if item["quantity"] <= remaining_to_remove:
            remaining_to_remove -= item["quantity"]
            item["status"] = "REMOVED"
        else:
            item["quantity"] -= remaining_to_remove
            remaining_to_remove = 0


def all_kitchen_items_completed_for_order(order_id):
    for item in kitchen_queue:
        if item["order_id"] == order_id and item["status"] == "PENDING":
            return False
    return True


def build_menu_response():
    menu_parts = []
    for category, items in MENU_ITEMS.items():
        menu_parts.append(category)
        for name, price in items:
            menu_parts.append(f"{name}:{price:.2f}")
    return "MENU|" + "|".join(menu_parts)


def build_tables_response():
    table_parts = []

    for order_id in sorted(orders.keys()):
        order = orders[order_id]
        if order["type"] == "DINE_IN" and order["status"] != "PAID":
            total = get_order_total(order)
            paid = money(order.get("paid_amount", 0.0))
            remaining = total - paid
            if remaining < money("0.00"):
                remaining = money("0.00")

            table_parts.append(
                f"Table:{order['table_number']},"
                f"OrderID:{order_id},"
                f"Guests:{order['guests']},"
                f"Status:{order['status']},"
                f"Remaining:{round_money(remaining):.2f}"
            )

    if not table_parts:
        return "TABLES|NONE"

    return "TABLES|" + "|".join(table_parts)


def build_orders_response():
    order_parts = []

    for order_id in sorted(orders.keys()):
        order = orders[order_id]
        total = get_order_total(order)
        paid = money(order.get("paid_amount", 0.0))
        remaining = total - paid
        if remaining < money("0.00"):
            remaining = money("0.00")

        if order["type"] == "DINE_IN":
            order_parts.append(
                f"OrderID:{order_id},"
                f"Type:DINE_IN,"
                f"Status:{order['status']},"
                f"Table:{order['table_number']},"
                f"Guests:{order['guests']},"
                f"Total:{round_money(total):.2f},"
                f"Paid:{round_money(paid):.2f},"
                f"Remaining:{round_money(remaining):.2f}"
            )
        else:
            order_parts.append(
                f"OrderID:{order_id},"
                f"Type:TAKE_OUT,"
                f"Status:{order['status']},"
                f"Name:{order.get('customer_name', '')},"
                f"Total:{round_money(total):.2f},"
                f"Paid:{round_money(paid):.2f},"
                f"Remaining:{round_money(remaining):.2f}"
            )

    if not order_parts:
        return "ORDERS|NONE"

    return "ORDERS|" + "|".join(order_parts)


def build_kitchen_queue_response(mode):
    if mode == "PENDING":
        items = [item for item in kitchen_queue if item["status"] == "PENDING"]
    else:
        items = list(kitchen_queue)

    items.sort(key=lambda x: (x["created_at"], x["kitchen_item_id"]))

    if not items:
        return "QUEUE|NONE"

    queue_parts = []
    for item in items:
        queue_parts.append(
            f"KitchenItemID:{item['kitchen_item_id']},"
            f"OrderID:{item['order_id']},"
            f"Table:{item['table_number']},"
            f"Seat:{item['seat_number']},"
            f"Name:{item.get('customer_name', '')},"
            f"Item:{item['item_name']},"
            f"Qty:{item['quantity']},"
            f"Time:{item['timestamp']},"
            f"Status:{item['status']}"
        )

    return "QUEUE|" + "|".join(queue_parts)


def get_item_kitchen_status(order_id, seat_number, item_name):
    if not is_kitchen_item(item_name):
        return "N/A"

    has_pending = False
    has_completed = False

    for item in kitchen_queue:
        if (
            item["order_id"] == order_id
            and item["seat_number"] == seat_number
            and item["item_name"].lower() == item_name.lower()
        ):
            if item["status"] == "PENDING":
                has_pending = True
            elif item["status"] == "COMPLETED":
                has_completed = True

    if has_pending:
        return "PENDING"
    if has_completed:
        return "READY"
    return "N/A"


def build_view_order_response(order_id):
    if order_id not in orders:
        return "ERROR|Invalid order ID"

    order = orders[order_id]
    total = get_order_total(order)
    paid = money(order.get("paid_amount", 0.0))
    remaining = total - paid
    if remaining < money("0.00"):
        remaining = money("0.00")

    parts = [
        "ORDERVIEW",
        f"OrderID:{order_id}",
        f"Type:{order['type']}",
        f"Status:{order['status']}",
        f"Total:{round_money(total):.2f}",
        f"Paid:{round_money(paid):.2f}",
        f"Remaining:{round_money(remaining):.2f}"
    ]

    if order["type"] == "DINE_IN":
        parts.append(f"Table:{order['table_number']}")
        parts.append(f"Guests:{order['guests']}")

        for seat_number in range(1, order["guests"] + 1):
            seat_total = get_seat_total(order, seat_number)
            seat_paid = money(order["seat_paid"].get(seat_number, 0.0))
            seat_remaining = seat_total - seat_paid
            if seat_remaining < money("0.00"):
                seat_remaining = money("0.00")

            parts.append(
                f"SEATTOTAL:{seat_number},{round_money(seat_total):.2f},{round_money(seat_paid):.2f},{round_money(seat_remaining):.2f}"
            )

        for seat_number in range(1, order["guests"] + 1):
            seat_items = order["seat_items"].get(seat_number, {})
            for item_name in sorted(seat_items.keys()):
                qty = seat_items[item_name]
                unit_price = get_item_price(item_name)
                line_total = unit_price * qty
                kitchen_status = get_item_kitchen_status(order_id, seat_number, item_name)

                parts.append(
                    f"ITEM:Seat {seat_number},{item_name},{qty},{round_money(unit_price):.2f},{round_money(line_total):.2f},{kitchen_status}"
                )
    else:
        parts.append(f"Name:{order.get('customer_name', '')}")
        for item_name in sorted(order["items"].keys()):
            qty = order["items"][item_name]
            unit_price = get_item_price(item_name)
            line_total = unit_price * qty
            kitchen_status = get_item_kitchen_status(order_id, 0, item_name)

            parts.append(
                f"ITEM:TAKE_OUT,{item_name},{qty},{round_money(unit_price):.2f},{round_money(line_total):.2f},{kitchen_status}"
            )

    return "|".join(parts)


def build_bill_response(order_id):
    if order_id not in orders:
        return "ERROR|Invalid order ID"

    order = orders[order_id]
    total = get_order_total(order)
    paid = money(order.get("paid_amount", 0.0))
    remaining = total - paid
    if remaining < money("0.00"):
        remaining = money("0.00")

    parts = [
        "BILL",
        f"OrderID:{order_id}",
        f"Type:{order['type']}",
        f"Total:{round_money(total):.2f}",
        f"Paid:{round_money(paid):.2f}",
        f"Remaining:{round_money(remaining):.2f}",
        f"Status:{order['status']}"
    ]

    if order["type"] == "DINE_IN":
        parts.append(f"Table:{order['table_number']}")
        for seat_number in range(1, order["guests"] + 1):
            seat_total = get_seat_total(order, seat_number)
            seat_paid = money(order["seat_paid"].get(seat_number, 0.0))
            seat_remaining = seat_total - seat_paid
            if seat_remaining < money("0.00"):
                seat_remaining = money("0.00")

            parts.append(
                f"Seat:{seat_number},{round_money(seat_total):.2f},{round_money(seat_paid):.2f},{round_money(seat_remaining):.2f}"
            )
    else:
        parts.append(f"Name:{order.get('customer_name', '')}")

    return "|".join(parts)


def process_cashout(order_id, pay_type, seat_value):
    if order_id not in orders:
        return "ERROR|Invalid order ID"

    order = orders[order_id]
    total = get_order_total(order)

    if order["type"] == "DINE_IN":
        if pay_type == "SEAT":
            try:
                seat_number = int(seat_value)
            except ValueError:
                return "ERROR|Invalid seat number"

            if seat_number < 1 or seat_number > order["guests"]:
                return "ERROR|Invalid seat number"

            seat_total = get_seat_total(order, seat_number)
            seat_paid = money(order["seat_paid"].get(seat_number, 0.0))
            seat_remaining = seat_total - seat_paid
            if seat_remaining < money("0.00"):
                seat_remaining = money("0.00")

            if seat_remaining == money("0.00"):
                return "ERROR|Seat already paid"

            order["seat_paid"][seat_number] = float(seat_total)
            order["paid_amount"] = float(money(order["paid_amount"]) + seat_remaining)
            amount_charged = seat_remaining

        elif pay_type == "TABLE":
            current_paid = money(order["paid_amount"])
            remaining = total - current_paid
            if remaining < money("0.00"):
                remaining = money("0.00")

            if remaining == money("0.00"):
                return "ERROR|Table already fully paid"

            for seat_number in range(1, order["guests"] + 1):
                order["seat_paid"][seat_number] = float(get_seat_total(order, seat_number))

            order["paid_amount"] = float(total)
            amount_charged = remaining
        else:
            return "ERROR|Invalid payment type"

    else:
        if pay_type != "TAKEOUT":
            return "ERROR|Invalid payment type"

        current_paid = money(order["paid_amount"])
        remaining = total - current_paid
        if remaining < money("0.00"):
            remaining = money("0.00")

        if remaining == money("0.00"):
            return "ERROR|Take-out order already fully paid"

        order["paid_amount"] = float(total)
        amount_charged = remaining

    recompute_order_status(order_id)

    total_paid = money(order["paid_amount"])
    remaining_balance = total - total_paid
    if remaining_balance < money("0.00"):
        remaining_balance = money("0.00")

    return (
        f"SUCCESS|Payment processed|"
        f"OrderID:{order_id}|"
        f"Charged:{round_money(amount_charged):.2f}|"
        f"Paid:{round_money(total_paid):.2f}|"
        f"Remaining:{round_money(remaining_balance):.2f}|"
        f"Status:{order['status']}"
    )


def process_command(command):
    global next_order_id

    parts = command.split("|")
    action = parts[0]

    if action == "LOGIN":
        if len(parts) != 3:
            return "ERROR|Invalid LOGIN format"

        username = parts[1]
        password = parts[2]

        if username in USERS and USERS[username] == password:
            token = f"{username}_session"
            sessions[token] = username
            return f"SUCCESS|Login successful|{token}"

        return "ERROR|Invalid credentials"

    elif action == "GETMENU":
        if len(parts) != 2:
            return "ERROR|Invalid GETMENU format"

        session_token = parts[1]
        if not is_authorized(session_token):
            return "ERROR|Unauthorized"

        return build_menu_response()

    elif action == "UPDATEPRICE":
        if len(parts) != 4:
            return "ERROR|Invalid UPDATEPRICE format"

        session_token = parts[1]
        item_name = parts[2]
        new_price_str = parts[3]

        _, err = require_role(session_token, {"MANAGER"})
        if err:
            return f"ERROR|{err}"

        item = find_menu_item(item_name)
        if item is None:
            return "ERROR|Invalid menu item"

        try:
            new_price = float(new_price_str)
        except ValueError:
            return "ERROR|Invalid price"

        if new_price < 0:
            return "ERROR|Invalid price"

        real_name, _, category = item

        updated_items = []
        for name, price in MENU_ITEMS[category]:
            if name == real_name:
                updated_items.append((name, new_price))
            else:
                updated_items.append((name, price))
        MENU_ITEMS[category] = updated_items

        return "SUCCESS|Price updated"

    elif action == "LISTTABLES":
        if len(parts) != 2:
            return "ERROR|Invalid LISTTABLES format"

        session_token = parts[1]
        _, err = require_role(session_token, {"MANAGER", "SERVER"})
        if err:
            return f"ERROR|{err}"

        return build_tables_response()

    elif action == "LISTORDERS":
        if len(parts) != 2:
            return "ERROR|Invalid LISTORDERS format"

        session_token = parts[1]
        _, err = require_role(session_token, {"MANAGER", "SERVER", "CHEF"})
        if err:
            return f"ERROR|{err}"

        return build_orders_response()

    elif action == "VIEWQUEUE":
        if len(parts) != 3:
            return "ERROR|Invalid VIEWQUEUE format"

        session_token = parts[1]
        mode = parts[2].upper()

        _, err = require_role(session_token, {"CHEF", "MANAGER"})
        if err:
            return f"ERROR|{err}"

        if mode not in {"PENDING", "ALL"}:
            return "ERROR|Invalid queue view mode"

        return build_kitchen_queue_response(mode)

    elif action == "COMPLETEITEM":
        if len(parts) != 3:
            return "ERROR|Invalid COMPLETEITEM format"

        session_token = parts[1]
        kitchen_item_id_str = parts[2]

        _, err = require_role(session_token, {"CHEF"})
        if err:
            return f"ERROR|{err}"

        try:
            kitchen_item_id = int(kitchen_item_id_str)
        except ValueError:
            return "ERROR|Invalid kitchen item ID"

        target_item = None
        for item in kitchen_queue:
            if item["kitchen_item_id"] == kitchen_item_id:
                target_item = item
                break

        if target_item is None:
            return "ERROR|Invalid kitchen item ID"

        if target_item["status"] == "COMPLETED":
            return "ERROR|Kitchen item already completed"

        if target_item["status"] == "REMOVED":
            return "ERROR|Kitchen item was removed"

        target_item["status"] = "COMPLETED"

        order_id = target_item["order_id"]
        if order_id in orders:
            recompute_order_status(order_id)

        return "SUCCESS|Kitchen item completed"

    elif action == "CREATEORDER":
        if len(parts) != 6:
            return "ERROR|Invalid CREATEORDER format"

        session_token = parts[1]
        order_type = parts[2]
        table_number_str = parts[3]
        guests_str = parts[4]
        customer_name = parts[5].strip()

        _, err = require_role(session_token, {"MANAGER", "SERVER"})
        if err:
            return f"ERROR|{err}"

        if order_type not in ["DINE_IN", "TAKE_OUT"]:
            return "ERROR|Invalid order type"

        if order_type == "DINE_IN":
            try:
                table_number = int(table_number_str)
            except ValueError:
                return "ERROR|Table number must be a number"

            if table_number < 1:
                return "ERROR|Invalid table number"

            try:
                guest_count = int(guests_str)
            except ValueError:
                return "ERROR|Guest count must be a number"

            if guest_count < 1 or guest_count > 4:
                return "ERROR|Invalid guest count (must be 1-4)"

            active_order_id, _ = get_active_dine_in_order_for_table(table_number)
            if active_order_id is not None:
                return "ERROR|Table already has an active order"

            customer_name = ""

        else:
            table_number = 0
            guest_count = 0

            if customer_name == "":
                return "ERROR|Take-out orders require customer name"

        next_order_id += 1

        orders[next_order_id] = {
            "type": order_type,
            "status": "PLACED",
            "table_number": table_number,
            "guests": guest_count,
            "customer_name": customer_name,
            "seat_items": {},
            "items": {},
            "paid_amount": 0.0,
            "seat_paid": {}
        }

        return (
            f"SUCCESS|Order created|"
            f"OrderID:{next_order_id}|"
            f"Type:{order_type}|"
            f"Table:{table_number}|"
            f"Guests:{guest_count}|"
            f"Name:{customer_name}"
        )

    elif action == "ADDITEM":
        if len(parts) != 6:
            return "ERROR|Invalid ADDITEM format"

        session_token = parts[1]
        order_id_str = parts[2]
        item_name = parts[3]
        qty_str = parts[4]
        seat_str = parts[5]

        _, err = require_role(session_token, {"MANAGER", "SERVER"})
        if err:
            return f"ERROR|{err}"

        try:
            order_id = int(order_id_str)
        except ValueError:
            return "ERROR|Invalid order ID"

        if order_id not in orders:
            return "ERROR|Invalid order ID"

        order = orders[order_id]

        if order["status"] == "PAID":
            return "ERROR|Cannot modify a paid order"

        try:
            qty = int(qty_str)
        except ValueError:
            return "ERROR|Quantity must be numeric"

        if qty < 1:
            return "ERROR|Quantity must be at least 1"

        real_name = normalize_item_name(item_name)
        if real_name is None:
            return "ERROR|Invalid menu item"

        if order["type"] == "DINE_IN":
            try:
                seat = int(seat_str)
            except ValueError:
                return "ERROR|Invalid seat number"

            if seat < 1 or seat > order["guests"]:
                return "ERROR|Invalid seat number"

            seat_map = order["seat_items"].setdefault(seat, {})
            seat_map[real_name] = seat_map.get(real_name, 0) + qty
            add_to_kitchen_queue(order_id, order, real_name, qty, seat)

        else:
            if seat_str != "0":
                return "ERROR|Seat number must be 0 for take-out"

            current_total_qty = sum(order["items"].values())
            if current_total_qty + qty > 10:
                return "ERROR|Take-out item limit exceeded (max 10)"

            seat = 0
            order["items"][real_name] = order["items"].get(real_name, 0) + qty
            add_to_kitchen_queue(order_id, order, real_name, qty, seat)

        recompute_order_status(order_id)

        return f"SUCCESS|Item added|{real_name}|Qty:{qty}|OrderID:{order_id}|Seat:{seat}"

    elif action == "REMOVEITEM":
        if len(parts) != 6:
            return "ERROR|Invalid REMOVEITEM format"

        session_token = parts[1]
        order_id_str = parts[2]
        item_name = parts[3]
        qty_str = parts[4]
        seat_str = parts[5]

        _, err = require_role(session_token, {"MANAGER", "SERVER"})
        if err:
            return f"ERROR|{err}"

        try:
            order_id = int(order_id_str)
        except ValueError:
            return "ERROR|Invalid order ID"

        if order_id not in orders:
            return "ERROR|Invalid order ID"

        try:
            qty = int(qty_str)
        except ValueError:
            return "ERROR|Quantity must be numeric"

        if qty < 1:
            return "ERROR|Quantity must be at least 1"

        order = orders[order_id]

        if order["status"] == "PAID":
            return "ERROR|Cannot modify a paid order"

        if order["type"] == "DINE_IN":
            try:
                seat = int(seat_str)
            except ValueError:
                return "ERROR|Invalid seat number"

            if seat < 1 or seat > order["guests"]:
                return "ERROR|Invalid seat number"

            seat_map = order["seat_items"].get(seat, {})
            matched_name = None

            for existing_item_name in seat_map.keys():
                if existing_item_name.lower() == item_name.lower():
                    matched_name = existing_item_name
                    break

            if matched_name is None:
                return "ERROR|Item not in order"

            current_qty = seat_map[matched_name]
            if qty > current_qty:
                return "ERROR|Remove quantity exceeds existing quantity"

            new_qty = current_qty - qty
            if new_qty == 0:
                del seat_map[matched_name]
            else:
                seat_map[matched_name] = new_qty

            if len(seat_map) == 0:
                order["seat_items"].pop(seat, None)

            remove_from_kitchen_queue(order_id, matched_name, qty, seat)

        else:
            if seat_str != "0":
                return "ERROR|Seat number must be 0 for take-out"

            seat = 0
            matched_name = None
            for existing_item_name in order["items"].keys():
                if existing_item_name.lower() == item_name.lower():
                    matched_name = existing_item_name
                    break

            if matched_name is None:
                return "ERROR|Item not in order"

            current_qty = order["items"][matched_name]
            if qty > current_qty:
                return "ERROR|Remove quantity exceeds existing quantity"

            new_qty = current_qty - qty
            if new_qty == 0:
                del order["items"][matched_name]
            else:
                order["items"][matched_name] = new_qty

            remove_from_kitchen_queue(order_id, matched_name, qty, 0)

        recompute_order_status(order_id)
        return "SUCCESS|Item removed"

    elif action == "VIEWORDER":
        if len(parts) != 3:
            return "ERROR|Invalid VIEWORDER format"

        session_token = parts[1]
        order_id_str = parts[2]

        _, err = require_role(session_token, {"MANAGER", "SERVER", "CHEF"})
        if err:
            return f"ERROR|{err}"

        try:
            order_id = int(order_id_str)
        except ValueError:
            return "ERROR|Invalid order ID"

        return build_view_order_response(order_id)

    elif action == "GETBILL":
        if len(parts) != 3:
            return "ERROR|Invalid GETBILL format"

        session_token = parts[1]
        order_id_str = parts[2]

        if not is_authorized(session_token):
            return "ERROR|Unauthorized"

        try:
            order_id = int(order_id_str)
        except ValueError:
            return "ERROR|Invalid order ID"

        return build_bill_response(order_id)

    elif action == "CASHOUT":
        if len(parts) != 5:
            return "ERROR|Invalid CASHOUT format"

        session_token = parts[1]
        order_id_str = parts[2]
        pay_type = parts[3].upper()
        seat_value = parts[4]

        _, err = require_role(session_token, {"MANAGER", "SERVER"})
        if err:
            return f"ERROR|{err}"

        try:
            order_id = int(order_id_str)
        except ValueError:
            return "ERROR|Invalid order ID"

        return process_cashout(order_id, pay_type, seat_value)

    elif action == "MARKORDERREADY":
        if len(parts) != 3:
            return "ERROR|Invalid MARKORDERREADY format"

        session_token = parts[1]
        order_id_str = parts[2]

        _, err = require_role(session_token, {"CHEF"})
        if err:
            return f"ERROR|{err}"

        try:
            order_id = int(order_id_str)
        except ValueError:
            return "ERROR|Invalid order ID"

        if order_id not in orders:
            return "ERROR|Invalid order ID"

        orders[order_id]["status"] = "READY"

        for item in kitchen_queue:
            if item["order_id"] == order_id and item["status"] == "PENDING":
                item["status"] = "COMPLETED"

        recompute_order_status(order_id)
        return "SUCCESS|Order marked ready"

    elif action == "LOGOUT":
        if len(parts) != 2:
            return "ERROR|Invalid LOGOUT format"

        session_token = parts[1]

        if session_token in sessions:
            del sessions[session_token]
            return "SUCCESS|Logged out"

        return "ERROR|Invalid session"

    else:
        return "ERROR|Unknown command"


def start_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(1)

    print("TCP Restaurant Server started")
    print("Waiting for client connection...")

    conn, addr = server_socket.accept()
    print(f"Client connected from {addr}")

    while True:
        data = conn.recv(4096)

        if not data:
            break

        message = data.decode().strip()
        print(f"Received: {safe_log_message(message)}")

        if message.lower() == "exit":
            conn.send("Server shutting down".encode())
            break

        response = process_command(message)
        print(f"Response: {response}")
        conn.send(response.encode())

    conn.close()
    server_socket.close()
    print("Server stopped")


if __name__ == "__main__":
    start_server()
