from dishka import Provider, Scope, provide

from app.telegram.dialogs.config_cache import DialogConfigCache


class DialogConfigProvider(Provider):
    scope = Scope.APP

    dialog_config_cache = provide(DialogConfigCache)
