from flask import Blueprint, render_template, request, jsonify, session, current_app
from flask_login import login_required, current_user

from app.decorators import roles_required
from app.models import Role
from app.ai.tools import TOOL_SCHEMAS, execute_tool

ai_bp = Blueprint("ai", __name__, template_folder="../templates/admin")

SYSTEM_PROMPT = (
    "You are the MediTrack assistant, built into a pharmacy/medical store "
    "management system. You help the shop administrator understand their "
    "stock, sales, wastage, and demand forecasts by calling the tools "
    "available to you and answering based on the real data returned — "
    "never guess or make up numbers. Keep answers concise and concrete "
    "(use actual figures from tool results). If a question needs a tool "
    "you don't have, say so plainly. Currency is Indian Rupees (Rs).\n\n"
    "One tool, create_purchase_order, writes real data (a new pending "
    "purchase order) rather than just looking things up. Only call it once "
    "the administrator has clearly asked you to place/create the order — "
    "if they're just asking whether something needs reordering, answer that "
    "with get_low_stock_items or get_item_forecast instead and ask if they'd "
    "like you to create the order, rather than creating it unprompted. "
    "After creating one, tell them it's Pending and won't add to stock "
    "until it's received (with a batch number and expiry date) on the "
    "Purchase Orders page."
)

MAX_TOOL_ROUNDS = 5


def _get_client():
    api_key = current_app.config.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=api_key)


@ai_bp.route("/assistant")
@login_required
@roles_required(Role.ADMIN)
def assistant_page():
    configured = bool(current_app.config.get("ANTHROPIC_API_KEY"))
    history = session.get("ai_chat_history", [])
    return render_template("admin/ai_assistant.html", configured=configured, history=history)


@ai_bp.route("/chat", methods=["POST"])
@login_required
@roles_required(Role.ADMIN)
def chat():
    client = _get_client()
    if client is None:
        return jsonify({"error": "ANTHROPIC_API_KEY is not set in your .env file. "
                                  "Add it and restart the app to enable the assistant."}), 400

    user_message = request.get_json().get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Empty message."}), 400

    messages = session.get("ai_chat_history", [])
    messages.append({"role": "user", "content": user_message})

    model = current_app.config.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    try:
        rounds = 0
        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )

            assistant_blocks = [block.model_dump() for block in response.content]
            messages.append({"role": "assistant", "content": assistant_blocks})

            if response.stop_reason != "tool_use":
                final_text = "".join(
                    b["text"] for b in assistant_blocks if b.get("type") == "text"
                )
                session["ai_chat_history"] = messages
                session.modified = True
                return jsonify({"reply": final_text})

            # Execute every tool call the model asked for, feed results back
            tool_results = []
            for block in assistant_blocks:
                if block.get("type") == "tool_use":
                    result = execute_tool(block["name"], block.get("input", {}))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": str(result),
                    })
            messages.append({"role": "user", "content": tool_results})

        return jsonify({"reply": "I had to look up more things than I could finish in one go — "
                                  "try asking a more specific question."})
    except Exception as exc:
        current_app.logger.exception("AI assistant error")
        return jsonify({"error": f"Assistant error: {exc}"}), 500


@ai_bp.route("/chat/reset", methods=["POST"])
@login_required
@roles_required(Role.ADMIN)
def reset_chat():
    session.pop("ai_chat_history", None)
    return jsonify({"success": True})
