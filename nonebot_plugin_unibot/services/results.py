def outcome(code="OK", hint="", *, status="ok", data=None, artifacts=None):
    return dict(status=status, code=code, data=data or {}, public_hint=hint,
                fallback_text=hint, artifacts=artifacts or [], effect="none", retryable=False)


def image(content):
    return dict(kind="image", content=content)
