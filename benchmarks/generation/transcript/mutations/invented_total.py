def total_tokens(event):
    usage = event.get("message", {}).get("usage", {})
    return usage.get("total_tokens", 0)
