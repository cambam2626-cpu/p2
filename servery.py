import grpc
from concurrent import futures
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import restaurant_pb2
import restaurant_pb2_grpc

users = {
    "manager": {"password": "pw", "role": "MANAGER"},
    "server1": {"password": "pw", "role": "SERVER"},
    "server2": {"password": "pw", "role": "SERVER"},
    "chef": {"password": "pw", "role": "CHEF"},
}

sessions = {}

orders = {}
next_order_id = 1000

kitchen_queue = []
next_kitchen_item_id = 1

menu_by_name = {
    "Fried Pickles": {"category": restaurant_pb2.STARTER, "price": 6.99, "available": True},
    "Wings": {"category": restaurant_pb2.STARTER, "price": 9.99, "available": True},
    "Stuffed Mushrooms": {"category": restaurant_pb2.STARTER, "price": 7.99, "available": True},

    "Cheeseburger": {"category": restaurant_pb2.MAIN, "price": 12.99, "available": True},
    "Chili Cheese Dog": {"category": restaurant_pb2.MAIN, "price": 10.99, "available": True},
    "Cuban Sandwich": {"category": restaurant_pb2.MAIN, "price": 13.99, "available": True},
    "Full Rack Ribs": {"category": restaurant_pb2.MAIN, "price": 18.99, "available": True},
    "Buffalo Chicken Sandwich": {"category": restaurant_pb2.MAIN, "price": 11.99, "available": True},

    "Chocolate Cake": {"category": restaurant_pb2.DESSERT, "price": 6.99, "available": True},
    "Banana Split": {"category": restaurant_pb2.DESSERT, "price": 7.99, "available": True},
    "Honey Ice Cream": {"category": restaurant_pb2.DESSERT, "price": 5.99, "available": True},

    "Coke": {"category": restaurant_pb2.DRINK, "price": 2.99, "available": True},
    "Sprite": {"category": restaurant_pb2.DRINK, "price": 2.99, "available": True},
    "Fanta": {"category": restaurant_pb2.DRINK, "price": 2.99, "available": True},
    "Water": {"category": restaurant_pb2.DRINK, "price": 0.00, "available": True},
    "Milkshake": {"category": restaurant_pb2.DRINK, "price": 4.99, "available": True},
}


def _money(value):
    return Decimal(str(value))


def _round_money(value):
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _get_session(session_token):
    return sessions.get(session_token)


def _require_role(session_token, allowed_roles):
    sess = _get_session(session_token)
    if not sess:
        return None, "Unauthorized"
    if sess["role"] not in allowed_roles:
        return None, "Forbidden"
    return sess, None


def _menu_as_repeated_items():
    items = []
    for name, rec in menu_by_name.items():
        items.append(
            restaurant_pb2.MenuItem(
                name=name,
                category=rec["category"],
                price=float(rec["price"]),
                available=bool(rec["available"]),
            )
        )
    items.sort(key=lambda m: (int(m.category), m.name))
    return items


def _normalize_item_name(item_name):
    for name in menu_by_name:
        if name.lower() == item_name.strip().lower():
            return name
    return None


def _is_kitchen_item(item_name):
    if item_name not in menu_by_name:
        return False
    return menu_by_name[item_name]["category"] != restaurant_pb2.DRINK


def _current_timestamp_string():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _find_order_by_table(table_number):
    for order_id, order in orders.items():
        if order["type"] == restaurant_pb2.DINE_IN and order["table_number"] == table_number:
            return order_id, order
    return None, None


def _get_order_total_decimal(order):
    total = Decimal("0.00")

    if order["type"] == restaurant_pb2.TAKE_OUT:
        for item_name, qty in order["items"].items():
            if item_name in menu_by_name:
                total += _money(menu_by_name[item_name]["price"]) * qty
        return total

    for seat_number in range(1, order["guests"] + 1):
        seat_items = order["seat_items"].get(seat_number, {})
        for item_name, qty in seat_items.items():
            if item_name in menu_by_name:
                total += _money(menu_by_name[item_name]["price"]) * qty

    return total


def _get_seat_total_decimal(order, seat_number):
    total = Decimal("0.00")
    seat_items = order["seat_items"].get(seat_number, {})
    for item_name, qty in seat_items.items():
        if item_name in menu_by_name:
            total += _money(menu_by_name[item_name]["price"]) * qty
    return total


def _recompute_order_status(order_id):
    order = orders[order_id]
    total = _get_order_total_decimal(order)
    paid = _money(order.get("paid_amount", 0.0))

    kitchen_pending_exists = False
    for item in kitchen_queue:
        if item["order_id"] == order_id and item["status"] == restaurant_pb2.PENDING:
            kitchen_pending_exists = True
            break

    if paid >= total and total > Decimal("0.00"):
        order["status"] = restaurant_pb2.PAID
        return

    if paid > Decimal("0.00"):
        order["status"] = restaurant_pb2.PARTIALLY_PAID
        return

    if kitchen_pending_exists:
        order["status"] = restaurant_pb2.PLACED
    else:
        order["status"] = restaurant_pb2.READY


def _add_to_kitchen_queue(order_id, order, item_name, quantity, seat_number):
    global next_kitchen_item_id

    if not _is_kitchen_item(item_name):
        return

    kitchen_queue.append({
        "kitchen_item_id": next_kitchen_item_id,
        "order_id": order_id,
        "table_number": order["table_number"],
        "seat_number": seat_number,
        "customer_name": order.get("customer_name", ""),
        "item_name": item_name,
        "quantity": quantity,
        "timestamp": _current_timestamp_string(),
        "created_at": time.time(),
        "status": restaurant_pb2.PENDING,
    })
    next_kitchen_item_id += 1


def _remove_from_kitchen_queue(order_id, item_name, quantity, seat_number):
    remaining_to_remove = quantity

    pending_items = sorted(
        [
            item for item in kitchen_queue
            if item["order_id"] == order_id
            and item["item_name"] == item_name
            and item["seat_number"] == seat_number
            and item["status"] == restaurant_pb2.PENDING
        ],
        key=lambda x: (x["created_at"], x["kitchen_item_id"])
    )

    for item in pending_items:
        if remaining_to_remove <= 0:
            break

        if item["quantity"] <= remaining_to_remove:
            remaining_to_remove -= item["quantity"]
            item["status"] = restaurant_pb2.COMPLETED
        else:
            item["quantity"] -= remaining_to_remove
            remaining_to_remove = 0


def _seat_kitchen_status(order_id, seat_number, item_name):
    has_pending = False
    has_completed = False

    for item in kitchen_queue:
        if (
            item["order_id"] == order_id
            and item["seat_number"] == seat_number
            and item["item_name"] == item_name
        ):
            if item["status"] == restaurant_pb2.PENDING:
                has_pending = True
            elif item["status"] == restaurant_pb2.COMPLETED:
                has_completed = True

    if has_pending:
        return True, restaurant_pb2.PENDING
    if has_completed:
        return True, restaurant_pb2.COMPLETED
    return False, restaurant_pb2.COMPLETED


def _all_kitchen_items_completed_for_order(order_id):
    for item in kitchen_queue:
        if item["order_id"] == order_id and item["status"] == restaurant_pb2.PENDING:
            return False
    return True


class RestaurantService(restaurant_pb2_grpc.RestaurantServiceServicer):

    def Login(self, request, context):
        if request.username in users and users[request.username]["password"] == request.password:
            token = f"{request.username}_session"
            sessions[token] = {
                "username": request.username,
                "role": users[request.username]["role"]
            }
            return restaurant_pb2.LoginResponse(
                success=True,
                message="Login successful",
                session_token=token
            )

        return restaurant_pb2.LoginResponse(
            success=False,
            message="Invalid credentials",
            session_token=""
        )

    def Logout(self, request, context):
        if request.session_token in sessions:
            del sessions[request.session_token]
            return restaurant_pb2.LogoutResponse(
                success=True,
                message="Logged out"
            )

        return restaurant_pb2.LogoutResponse(
            success=False,
            message="Invalid session token"
        )

    def GetMenu(self, request, context):
        if request.session_token not in sessions:
            return restaurant_pb2.MenuResponse(
                success=False,
                message="Unauthorized",
                items=[]
            )

        return restaurant_pb2.MenuResponse(
            success=True,
            message="Menu retrieved",
            items=_menu_as_repeated_items()
        )

    def UpdatePrice(self, request, context):
        _, err = _require_role(request.session_token, {"MANAGER"})
        if err:
            return restaurant_pb2.UpdatePriceResponse(
                success=False,
                message=err
            )

        canonical_name = _normalize_item_name(request.item_name)
        if canonical_name is None:
            return restaurant_pb2.UpdatePriceResponse(
                success=False,
                message="Invalid menu item"
            )

        if request.new_price < 0:
            return restaurant_pb2.UpdatePriceResponse(
                success=False,
                message="Invalid price"
            )

        menu_by_name[canonical_name]["price"] = float(request.new_price)

        return restaurant_pb2.UpdatePriceResponse(
            success=True,
            message="Price updated"
        )

    def CreateOrder(self, request, context):
        global next_order_id

        _, err = _require_role(request.session_token, {"MANAGER", "SERVER"})
        if err:
            return restaurant_pb2.CreateOrderResponse(
                success=False,
                message=err,
                order_id=0
            )

        if request.type == restaurant_pb2.DINE_IN:
            if request.guests < 1 or request.guests > 4:
                return restaurant_pb2.CreateOrderResponse(
                    success=False,
                    message="Invalid guest count (must be 1-4)",
                    order_id=0
                )

            if request.table_number < 1:
                return restaurant_pb2.CreateOrderResponse(
                    success=False,
                    message="Invalid table number",
                    order_id=0
                )

            for oid, order in orders.items():
                if (
                    order["type"] == restaurant_pb2.DINE_IN
                    and order["table_number"] == request.table_number
                    and order["status"] != restaurant_pb2.PAID
                ):
                    return restaurant_pb2.CreateOrderResponse(
                        success=False,
                        message="Table already has an active order",
                        order_id=0
                    )

        elif request.type == restaurant_pb2.TAKE_OUT:
            if not request.customer_name.strip():
                return restaurant_pb2.CreateOrderResponse(
                    success=False,
                    message="Take-out orders require customer name",
                    order_id=0
                )
        else:
            return restaurant_pb2.CreateOrderResponse(
                success=False,
                message="Invalid order type",
                order_id=0
            )

        next_order_id += 1
        order_id = next_order_id

        orders[order_id] = {
            "type": request.type,
            "status": restaurant_pb2.PLACED,
            "table_number": request.table_number if request.type == restaurant_pb2.DINE_IN else 0,
            "guests": request.guests if request.type == restaurant_pb2.DINE_IN else 0,
            "customer_name": request.customer_name.strip() if request.type == restaurant_pb2.TAKE_OUT else "",
            "seat_items": {},
            "items": {},
            "paid_amount": 0.0,
            "seat_paid": {},
        }

        return restaurant_pb2.CreateOrderResponse(
            success=True,
            message="Order created",
            order_id=order_id
        )

    def AddItem(self, request, context):
        _, err = _require_role(request.session_token, {"MANAGER", "SERVER"})
        if err:
            return restaurant_pb2.OrderResponse(success=False, message=err)

        if request.order_id not in orders:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Invalid order identifier"
            )

        if request.quantity < 1:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Invalid quantity"
            )

        order = orders[request.order_id]

        canonical_name = _normalize_item_name(request.item_name)
        if canonical_name is None:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Invalid menu item"
            )

        rec = menu_by_name[canonical_name]
        if not rec["available"]:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Item unavailable"
            )

        if order["status"] == restaurant_pb2.PAID:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Cannot modify a paid order"
            )

        if order["type"] == restaurant_pb2.DINE_IN:
            guests = order["guests"]
            if request.seat_number < 1 or request.seat_number > guests:
                return restaurant_pb2.OrderResponse(
                    success=False,
                    message="Invalid seat number"
                )

            seat_map = order["seat_items"].setdefault(request.seat_number, {})
            seat_map[canonical_name] = seat_map.get(canonical_name, 0) + request.quantity

            _add_to_kitchen_queue(
                request.order_id,
                order,
                canonical_name,
                request.quantity,
                request.seat_number
            )
            _recompute_order_status(request.order_id)

            return restaurant_pb2.OrderResponse(
                success=True,
                message="Item added"
            )

        if request.seat_number != 0:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Seat number must be 0 for take-out"
            )

        current_total_qty = sum(order["items"].values())
        if current_total_qty + request.quantity > 10:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Take-out item limit exceeded (max 10)"
            )

        order["items"][canonical_name] = order["items"].get(canonical_name, 0) + request.quantity

        _add_to_kitchen_queue(
            request.order_id,
            order,
            canonical_name,
            request.quantity,
            0
        )
        _recompute_order_status(request.order_id)

        return restaurant_pb2.OrderResponse(
            success=True,
            message="Item added"
        )

    def RemoveItem(self, request, context):
        _, err = _require_role(request.session_token, {"MANAGER", "SERVER"})
        if err:
            return restaurant_pb2.OrderResponse(success=False, message=err)

        if request.order_id not in orders:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Invalid order identifier"
            )

        if request.quantity < 1:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Invalid quantity"
            )

        order = orders[request.order_id]

        canonical_name = _normalize_item_name(request.item_name)
        if canonical_name is None:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Invalid menu item"
            )

        if order["status"] == restaurant_pb2.PAID:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Cannot modify a paid order"
            )

        if order["type"] == restaurant_pb2.DINE_IN:
            guests = order["guests"]
            if request.seat_number < 1 or request.seat_number > guests:
                return restaurant_pb2.OrderResponse(
                    success=False,
                    message="Invalid seat number"
                )

            seat_map = order["seat_items"].get(request.seat_number, {})
            if canonical_name not in seat_map:
                return restaurant_pb2.OrderResponse(
                    success=False,
                    message="Item not in order"
                )

            current_qty = seat_map[canonical_name]
            if request.quantity > current_qty:
                return restaurant_pb2.OrderResponse(
                    success=False,
                    message="Remove quantity exceeds existing quantity"
                )

            new_qty = current_qty - request.quantity
            if new_qty == 0:
                del seat_map[canonical_name]
            else:
                seat_map[canonical_name] = new_qty

            if len(seat_map) == 0:
                order["seat_items"].pop(request.seat_number, None)

            _remove_from_kitchen_queue(
                request.order_id,
                canonical_name,
                request.quantity,
                request.seat_number
            )
            _recompute_order_status(request.order_id)

            return restaurant_pb2.OrderResponse(
                success=True,
                message="Item removed"
            )

        if request.seat_number != 0:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Seat number must be 0 for take-out"
            )

        items = order["items"]
        if canonical_name not in items:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Item not in order"
            )

        current_qty = items[canonical_name]
        if request.quantity > current_qty:
            return restaurant_pb2.OrderResponse(
                success=False,
                message="Remove quantity exceeds existing quantity"
            )

        new_qty = current_qty - request.quantity
        if new_qty == 0:
            del items[canonical_name]
        else:
            items[canonical_name] = new_qty

        _remove_from_kitchen_queue(
            request.order_id,
            canonical_name,
            request.quantity,
            0
        )
        _recompute_order_status(request.order_id)

        return restaurant_pb2.OrderResponse(
            success=True,
            message="Item removed"
        )

    def GetBill(self, request, context):
        if request.session_token not in sessions:
            return restaurant_pb2.BillResponse(
                success=False,
                message="Unauthorized",
                order_id=0,
                total=0.0,
                paid=0.0,
                remaining=0.0,
                seat_totals=[],
                fully_paid=False
            )

        if request.order_id not in orders:
            return restaurant_pb2.BillResponse(
                success=False,
                message="Invalid order identifier",
                order_id=0,
                total=0.0,
                paid=0.0,
                remaining=0.0,
                seat_totals=[],
                fully_paid=False
            )

        order = orders[request.order_id]
        total = _get_order_total_decimal(order)
        paid = _money(order.get("paid_amount", 0.0))
        remaining = total - paid
        if remaining < Decimal("0.00"):
            remaining = Decimal("0.00")

        seat_totals = []

        if order["type"] == restaurant_pb2.DINE_IN:
            for seat_number in range(1, order["guests"] + 1):
                seat_total = _get_seat_total_decimal(order, seat_number)
                seat_paid = _money(order["seat_paid"].get(seat_number, 0.0))
                seat_remaining = seat_total - seat_paid
                if seat_remaining < Decimal("0.00"):
                    seat_remaining = Decimal("0.00")

                seat_totals.append(
                    restaurant_pb2.SeatTotal(
                        seat_number=seat_number,
                        total=_round_money(seat_total),
                        paid=_round_money(seat_paid),
                        remaining=_round_money(seat_remaining),
                        fully_paid=(seat_remaining == Decimal("0.00"))
                    )
                )

        return restaurant_pb2.BillResponse(
            success=True,
            message="Bill calculated",
            order_id=request.order_id,
            type=order["type"],
            table_number=order["table_number"],
            customer_name=order.get("customer_name", ""),
            total=_round_money(total),
            paid=_round_money(paid),
            remaining=_round_money(remaining),
            seat_totals=seat_totals,
            fully_paid=(remaining == Decimal("0.00"))
        )

    def ListOrders(self, request, context):
        _, err = _require_role(request.session_token, {"MANAGER", "SERVER", "CHEF"})
        if err:
            return restaurant_pb2.ListOrdersResponse(
                success=False,
                message=err,
                orders=[]
            )

        summaries = []
        for order_id, order in sorted(orders.items()):
            total = _get_order_total_decimal(order)
            paid = _money(order.get("paid_amount", 0.0))
            remaining = total - paid
            if remaining < Decimal("0.00"):
                remaining = Decimal("0.00")

            summaries.append(
                restaurant_pb2.OrderSummary(
                    order_id=order_id,
                    type=order["type"],
                    status=order["status"],
                    table_number=order["table_number"],
                    guests=order["guests"],
                    customer_name=order.get("customer_name", ""),
                    total=_round_money(total),
                    paid=_round_money(paid),
                    remaining=_round_money(remaining)
                )
            )

        return restaurant_pb2.ListOrdersResponse(
            success=True,
            message="Orders listed",
            orders=summaries
        )

    def MarkOrderReady(self, request, context):
        _, err = _require_role(request.session_token, {"CHEF"})
        if err:
            return restaurant_pb2.MarkOrderReadyResponse(
                success=False,
                message=err
            )

        if request.order_id not in orders:
            return restaurant_pb2.MarkOrderReadyResponse(
                success=False,
                message="Invalid order identifier"
            )

        orders[request.order_id]["status"] = restaurant_pb2.READY
        return restaurant_pb2.MarkOrderReadyResponse(
            success=True,
            message="Order marked ready"
        )

    def ViewKitchenQueue(self, request, context):
        _, err = _require_role(request.session_token, {"CHEF", "MANAGER"})
        if err:
            return restaurant_pb2.ViewKitchenQueueResponse(
                success=False,
                message=err,
                items=[]
            )

        if request.view_type == restaurant_pb2.PENDING_ONLY:
            items = [item for item in kitchen_queue if item["status"] == restaurant_pb2.PENDING]
        else:
            items = list(kitchen_queue)

        items.sort(key=lambda item: (item["created_at"], item["kitchen_item_id"]))

        response_items = []
        for item in items:
            response_items.append(
                restaurant_pb2.KitchenItem(
                    kitchen_item_id=item["kitchen_item_id"],
                    order_id=item["order_id"],
                    table_number=item["table_number"],
                    seat_number=item["seat_number"],
                    customer_name=item.get("customer_name", ""),
                    item_name=item["item_name"],
                    quantity=item["quantity"],
                    timestamp=item["timestamp"],
                    status=item["status"]
                )
            )

        return restaurant_pb2.ViewKitchenQueueResponse(
            success=True,
            message="Kitchen queue listed",
            items=response_items
        )

    def CompleteKitchenItem(self, request, context):
        _, err = _require_role(request.session_token, {"CHEF"})
        if err:
            return restaurant_pb2.CompleteKitchenItemResponse(
                success=False,
                message=err
            )

        target_item = None
        for item in kitchen_queue:
            if item["kitchen_item_id"] == request.kitchen_item_id:
                target_item = item
                break

        if target_item is None:
            return restaurant_pb2.CompleteKitchenItemResponse(
                success=False,
                message="Invalid kitchen item identifier"
            )

        if target_item["status"] == restaurant_pb2.COMPLETED:
            return restaurant_pb2.CompleteKitchenItemResponse(
                success=False,
                message="Kitchen item already completed"
            )

        target_item["status"] = restaurant_pb2.COMPLETED

        order_id = target_item["order_id"]
        if order_id in orders:
            _recompute_order_status(order_id)

        return restaurant_pb2.CompleteKitchenItemResponse(
            success=True,
            message="Kitchen item completed"
        )

    def ViewOrder(self, request, context):
        _, err = _require_role(request.session_token, {"MANAGER", "SERVER", "CHEF"})
        if err:
            return restaurant_pb2.ViewOrderResponse(
                success=False,
                message=err,
                order_id=0,
                total=0.0,
                paid=0.0,
                remaining=0.0,
                seat_totals=[],
                items=[]
            )

        if request.order_id not in orders:
            return restaurant_pb2.ViewOrderResponse(
                success=False,
                message="Invalid order identifier",
                order_id=0,
                total=0.0,
                paid=0.0,
                remaining=0.0,
                seat_totals=[],
                items=[]
            )

        order = orders[request.order_id]
        total = _get_order_total_decimal(order)
        paid = _money(order.get("paid_amount", 0.0))
        remaining = total - paid
        if remaining < Decimal("0.00"):
            remaining = Decimal("0.00")

        seat_totals = []
        detail_items = []

        if order["type"] == restaurant_pb2.DINE_IN:
            for seat_number in range(1, order["guests"] + 1):
                seat_total = _get_seat_total_decimal(order, seat_number)
                seat_paid = _money(order["seat_paid"].get(seat_number, 0.0))
                seat_remaining = seat_total - seat_paid
                if seat_remaining < Decimal("0.00"):
                    seat_remaining = Decimal("0.00")

                seat_totals.append(
                    restaurant_pb2.SeatTotal(
                        seat_number=seat_number,
                        total=_round_money(seat_total),
                        paid=_round_money(seat_paid),
                        remaining=_round_money(seat_remaining),
                        fully_paid=(seat_remaining == Decimal("0.00"))
                    )
                )

                seat_items = order["seat_items"].get(seat_number, {})
                for item_name, qty in sorted(seat_items.items()):
                    unit_price = _money(menu_by_name[item_name]["price"])
                    sent_to_kitchen, kitchen_status = _seat_kitchen_status(request.order_id, seat_number, item_name)

                    detail_items.append(
                        restaurant_pb2.OrderItemDetail(
                            item_name=item_name,
                            quantity=qty,
                            unit_price=_round_money(unit_price),
                            line_total=_round_money(unit_price * qty),
                            seat_number=seat_number,
                            sent_to_kitchen=sent_to_kitchen,
                            kitchen_status=kitchen_status
                        )
                    )
        else:
            for item_name, qty in sorted(order["items"].items()):
                unit_price = _money(menu_by_name[item_name]["price"])
                sent_to_kitchen, kitchen_status = _seat_kitchen_status(request.order_id, 0, item_name)

                detail_items.append(
                    restaurant_pb2.OrderItemDetail(
                        item_name=item_name,
                        quantity=qty,
                        unit_price=_round_money(unit_price),
                        line_total=_round_money(unit_price * qty),
                        seat_number=0,
                        sent_to_kitchen=sent_to_kitchen,
                        kitchen_status=kitchen_status
                    )
                )

        return restaurant_pb2.ViewOrderResponse(
            success=True,
            message="Order viewed",
            order_id=request.order_id,
            type=order["type"],
            status=order["status"],
            table_number=order["table_number"],
            guests=order["guests"],
            customer_name=order.get("customer_name", ""),
            total=_round_money(total),
            paid=_round_money(paid),
            remaining=_round_money(remaining),
            seat_totals=seat_totals,
            items=detail_items
        )

    def Cashout(self, request, context):
        _, err = _require_role(request.session_token, {"MANAGER", "SERVER"})
        if err:
            return restaurant_pb2.CashoutResponse(
                success=False,
                message=err,
                order_id=0,
                amount_charged=0.0,
                total_paid=0.0,
                remaining_balance=0.0,
                status=restaurant_pb2.PLACED
            )

        if request.order_id not in orders:
            return restaurant_pb2.CashoutResponse(
                success=False,
                message="Invalid order identifier",
                order_id=0,
                amount_charged=0.0,
                total_paid=0.0,
                remaining_balance=0.0,
                status=restaurant_pb2.PLACED
            )

        order = orders[request.order_id]
        total = _get_order_total_decimal(order)
        amount_charged = Decimal("0.00")

        if order["type"] == restaurant_pb2.DINE_IN:
            if request.payment_target == restaurant_pb2.SEAT:
                if request.seat_number < 1 or request.seat_number > order["guests"]:
                    return restaurant_pb2.CashoutResponse(
                        success=False,
                        message="Invalid seat number",
                        order_id=request.order_id,
                        amount_charged=0.0,
                        total_paid=_round_money(_money(order["paid_amount"])),
                        remaining_balance=_round_money(total - _money(order["paid_amount"])),
                        status=order["status"]
                    )

                seat_total = _get_seat_total_decimal(order, request.seat_number)
                seat_paid = _money(order["seat_paid"].get(request.seat_number, 0.0))
                seat_remaining = seat_total - seat_paid
                if seat_remaining < Decimal("0.00"):
                    seat_remaining = Decimal("0.00")

                if seat_remaining == Decimal("0.00"):
                    return restaurant_pb2.CashoutResponse(
                        success=False,
                        message="Seat already paid",
                        order_id=request.order_id,
                        amount_charged=0.0,
                        total_paid=_round_money(_money(order["paid_amount"])),
                        remaining_balance=_round_money(total - _money(order["paid_amount"])),
                        status=order["status"]
                    )

                order["seat_paid"][request.seat_number] = float(seat_total)
                order["paid_amount"] = float(_money(order["paid_amount"]) + seat_remaining)
                amount_charged = seat_remaining

            elif request.payment_target == restaurant_pb2.TABLE:
                current_paid = _money(order["paid_amount"])
                remaining = total - current_paid
                if remaining < Decimal("0.00"):
                    remaining = Decimal("0.00")

                if remaining == Decimal("0.00"):
                    return restaurant_pb2.CashoutResponse(
                        success=False,
                        message="Table already fully paid",
                        order_id=request.order_id,
                        amount_charged=0.0,
                        total_paid=_round_money(current_paid),
                        remaining_balance=0.0,
                        status=order["status"]
                    )

                for seat_number in range(1, order["guests"] + 1):
                    seat_total = _get_seat_total_decimal(order, seat_number)
                    order["seat_paid"][seat_number] = float(seat_total)

                order["paid_amount"] = float(total)
                amount_charged = remaining

            else:
                return restaurant_pb2.CashoutResponse(
                    success=False,
                    message="Invalid payment target for dine-in",
                    order_id=request.order_id,
                    amount_charged=0.0,
                    total_paid=_round_money(_money(order["paid_amount"])),
                    remaining_balance=_round_money(total - _money(order["paid_amount"])),
                    status=order["status"]
                )

        else:
            if request.payment_target != restaurant_pb2.TAKEOUT_ORDER:
                return restaurant_pb2.CashoutResponse(
                    success=False,
                    message="Invalid payment target for take-out",
                    order_id=request.order_id,
                    amount_charged=0.0,
                    total_paid=_round_money(_money(order["paid_amount"])),
                    remaining_balance=_round_money(total - _money(order["paid_amount"])),
                    status=order["status"]
                )

            current_paid = _money(order["paid_amount"])
            remaining = total - current_paid
            if remaining < Decimal("0.00"):
                remaining = Decimal("0.00")

            if remaining == Decimal("0.00"):
                return restaurant_pb2.CashoutResponse(
                    success=False,
                    message="Take-out order already fully paid",
                    order_id=request.order_id,
                    amount_charged=0.0,
                    total_paid=_round_money(current_paid),
                    remaining_balance=0.0,
                    status=order["status"]
                )

            order["paid_amount"] = float(total)
            amount_charged = remaining

        _recompute_order_status(request.order_id)

        total_paid = _money(order["paid_amount"])
        remaining_balance = total - total_paid
        if remaining_balance < Decimal("0.00"):
            remaining_balance = Decimal("0.00")

        return restaurant_pb2.CashoutResponse(
            success=True,
            message="Payment processed",
            order_id=request.order_id,
            amount_charged=_round_money(amount_charged),
            total_paid=_round_money(total_paid),
            remaining_balance=_round_money(remaining_balance),
            status=order["status"]
        )


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    restaurant_pb2_grpc.add_RestaurantServiceServicer_to_server(
        RestaurantService(),
        server
    )
    server.add_insecure_port("[::]:50051")
    server.start()
    print("Server started on port 50051")

    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == "__main__":
    serve()
