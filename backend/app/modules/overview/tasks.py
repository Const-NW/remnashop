from dishka.integrations.taskiq import FromDishka, inject

from app.core.broker import broker
from app.modules.overview.usecases import SampleRemnawaveOnlineUseCase, SendDailyReportUseCase


@broker.task(schedule=[{"cron": "*/5 * * * *"}])
@inject(patch_module=True)
async def sample_remnawave_online_task(use_case: FromDishka[SampleRemnawaveOnlineUseCase]) -> None:
    await use_case.system()


# hourly: the report goes out in the hour set in the shop's own time zone
@broker.task(schedule=[{"cron": "5 * * * *"}])
@inject(patch_module=True)
async def send_daily_report_task(use_case: FromDishka[SendDailyReportUseCase]) -> None:
    await use_case.system()
