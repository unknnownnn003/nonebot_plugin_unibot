"""Match NoneBot's original SUPERUSER matcher, including adapter-qualified IDs."""


def native_superuser(bot, event):
    try:
        user = event.get_user_id()
    except AttributeError:
        user = str(event.user_id)
    if user in bot.config.superusers:
        return True
    adapter = getattr(bot, "adapter", None)
    if adapter is None:
        return False
    return f"{adapter.get_name().split(maxsplit=1)[0].lower()}:{user}" in bot.config.superusers
