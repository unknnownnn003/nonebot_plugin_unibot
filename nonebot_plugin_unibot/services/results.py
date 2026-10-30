def outcome(code="OK", hint="", *, status="ok", data=None, artifacts=None):
    return dict(status=status, code=code, data=data or {}, public_hint=hint,
                fallback_text=hint, artifacts=artifacts or [], effect="none", retryable=False)


def image(content):
    return dict(kind="image", content=content)


async def send_command_result(matcher, result, *, finish=False):
    """Keep queue bookkeeping internal; commands present only content or errors."""
    accepted = result.get("code") in {"JOB_ACCEPTED", "JOB_EXISTS"}
    text = None if accepted else result.get("fallback_text") or result.get("public_hint")
    if finish:
        if text:
            await matcher.finish(text)
        else:
            await matcher.finish()
    elif text:
        await matcher.send(text)


def redundant_media_notice(job):
    return job.status == "succeeded" and job.result.get("data", {}).get("artifact_delivery") == "sent"
