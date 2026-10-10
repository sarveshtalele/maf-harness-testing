def total_tokens(event):
    usage = event.get("message", {}).get("usage", {})
    return usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
