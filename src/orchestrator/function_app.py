"""Azure Functions 진입점(Python v2 모델). 업무 로직은 journey 패키지에 있다."""

import logging
import os
from datetime import UTC, datetime, timedelta

import azure.durable_functions as df
import azure.functions as func

from agents.pretranslate import SOURCES
from journey import telemetry
from journey.activities import ACTIVITIES, Deps
from journey.consent import CONSENT_ACTIVITIES, handle_consent_command, handle_lifecycle_start, sweep_raw_repairs
from journey.consent import consent_lifecycle as lifecycle_steps
from journey.entry import handle_decision, handle_start
from journey.grievance import handle_grievance, handle_grievance_decision
from journey.metering import run_metered
from journey.monitoring import (
    TOPIC_FLOW,
    TOPIC_REPAYMENT,
    handle_flow_update,
    handle_replay,
    handle_repayment_updated,
    handle_restructure_decision,
)
from journey.orchestration import journey
from journey.pretranslate import handle_changes

telemetry.setup()

log = logging.getLogger("journey.pretranslate")
app = df.DFApp(http_auth_level=func.AuthLevel.FUNCTION)
RETRY = df.RetryOptions(first_retry_interval_in_milliseconds=5000, max_number_of_attempts=3)
SB_CONNECTION = "ServiceBusConnection"
_deps: Deps | None = None


def deps() -> Deps:
    global _deps
    if _deps is None:
        from journey.adapters import build_deps

        _deps = build_deps()
    return _deps


def _metered_activity(name: str, payload: dict) -> dict:
    return run_metered(name, ACTIVITIES[name], deps(), payload)


@app.queue_trigger(arg_name="msg", queue_name="applications-start", connection="AzureWebJobsStorage")
@app.durable_client_input(client_name="client")
async def start_application(msg: func.QueueMessage, client: df.DurableOrchestrationClient) -> None:
    await handle_start(client, msg.get_body().decode("utf-8"))


@app.queue_trigger(arg_name="msg", queue_name="officer-decisions", connection="AzureWebJobsStorage")
@app.durable_client_input(client_name="client")
async def officer_decision(msg: func.QueueMessage, client: df.DurableOrchestrationClient) -> None:
    await handle_decision(client, msg.get_body().decode("utf-8"))


@app.orchestration_trigger(context_name="context")
def application_journey(context: df.DurableOrchestrationContext):
    return (yield from journey(context, RETRY))


@app.activity_trigger(input_name="payload")
def save_document(payload: dict) -> dict:
    return ACTIVITIES["save_document"](deps(), payload)


@app.activity_trigger(input_name="payload")
def request_consent(payload: dict) -> dict:
    return _metered_activity("request_consent", payload)


@app.activity_trigger(input_name="payload")
def assemble_data(payload: dict) -> dict:
    return _metered_activity("assemble_data", payload)


@app.activity_trigger(input_name="payload")
def underwrite(payload: dict) -> dict:
    return _metered_activity("underwrite", payload)


@app.activity_trigger(input_name="payload")
def evaluate_policy(payload: dict) -> dict:
    return _metered_activity("evaluate_policy", payload)


@app.activity_trigger(input_name="payload")
def assess_credit(payload: dict) -> dict:
    return _metered_activity("assess_credit", payload)


@app.activity_trigger(input_name="payload")
def register_monitoring(payload: dict) -> dict:
    return ACTIVITIES["register_monitoring"](deps(), payload)


@app.queue_trigger(arg_name="msg", queue_name="consent-lifecycle", connection="AzureWebJobsStorage")
@app.durable_client_input(client_name="client")
async def consent_lifecycle_start(msg: func.QueueMessage, client: df.DurableOrchestrationClient) -> None:
    await handle_lifecycle_start(client, msg.get_body().decode("utf-8"), deps().store)


@app.queue_trigger(arg_name="msg", queue_name="consent-commands", connection="AzureWebJobsStorage")
@app.durable_client_input(client_name="client")
async def consent_command(msg: func.QueueMessage, client: df.DurableOrchestrationClient) -> None:
    await handle_consent_command(client, msg.get_body().decode("utf-8"), deps().store)


@app.orchestration_trigger(context_name="context")
def consent_lifecycle(context: df.DurableOrchestrationContext):
    # 함수 이름이 오케스트레이터 이름이다(LIFECYCLE_ORCHESTRATOR).
    return (yield from lifecycle_steps(context, RETRY))


@app.activity_trigger(input_name="payload")
def delete_consent_data(payload: dict) -> dict:
    return CONSENT_ACTIVITIES["delete_consent_data"](deps(), payload)


@app.timer_trigger(arg_name="timer", schedule="0 */5 * * * *", use_monitor=True)
def repair_consent_deletions(timer: func.TimerRequest) -> None:
    sweep_raw_repairs(deps())


@app.activity_trigger(input_name="payload")
def send_consent_notice(payload: dict) -> dict:
    return CONSENT_ACTIVITIES["send_consent_notice"](deps(), payload)


@app.queue_trigger(arg_name="msg", queue_name="grievances", connection="AzureWebJobsStorage")
def grievance_submitted(msg: func.QueueMessage) -> None:
    handle_grievance(deps(), msg.get_body().decode("utf-8"))


@app.queue_trigger(arg_name="msg", queue_name="grievance-decisions", connection="AzureWebJobsStorage")
def grievance_decision(msg: func.QueueMessage) -> None:
    handle_grievance_decision(deps(), msg.get_body().decode("utf-8"))


@app.queue_trigger(arg_name="msg", queue_name="monitoring-replay", connection="AzureWebJobsStorage")
def monitoring_replay(msg: func.QueueMessage) -> None:
    if not msg.id:
        raise RuntimeError("Replay command has no queue message ID")
    handle_replay(deps(), msg.get_body().decode("utf-8"), command_id=msg.id)


@app.queue_trigger(arg_name="msg", queue_name="restructure-decisions", connection="AzureWebJobsStorage")
def restructure_decision(msg: func.QueueMessage) -> None:
    handle_restructure_decision(deps(), msg.get_body().decode("utf-8"))


COSMOS_CONNECTION = "CosmosDBConnection"
PRETRANSLATE_BACKFILL = timedelta(days=7)
# 리스 세대를 올리면 새 리스가 만들어져 start_from_time부터 최근 7일을 다시 읽는다. 이미 저장된 문구는 건너뛰어 빠진 문구만 모델에 간다.
LEASE_GENERATION = 2
# 429가 잦은 백필에서도 한 호출이 짧게 끝나도록 묶음 크기를 줄인다.
PRETRANSLATE_MAX_ITEMS = 20


def _pretranslate(source: str, docs) -> None:
    """변경 피드 한 묶음을 번역한다. 어떤 예외도 올리지 않는다(올리면 같은 묶음이 계속 재시도된다)."""
    try:
        handle_changes(deps(), source, [d.to_dict() for d in docs])
    except Exception as e:  # noqa: BLE001 - 예외 본문·트레이스백에 문서 값이 있을 수 있어 종류만 남긴다
        log.error("pretranslate trigger failed source=%s error=%s", source, type(e).__name__)


def _register_pretranslate(source: str, start_from: str) -> None:
    @app.function_name(name=f"pretranslate_{source}")
    @app.cosmos_db_trigger(
        arg_name="docs",
        connection=COSMOS_CONNECTION,
        database_name="%COSMOS_DATABASE%",
        container_name=source,
        lease_container_name="leases",
        lease_container_prefix=f"{source}-g{LEASE_GENERATION}-",
        create_lease_container_if_not_exists=False,
        start_from_time=start_from,
        max_items_per_invocation=PRETRANSLATE_MAX_ITEMS,
    )
    def run(docs: func.DocumentList) -> None:
        _pretranslate(source, docs)


# 변경 피드 연결(ID 기반 CosmosDBConnection__accountEndpoint)이 있을 때만 등록한다. 시작 시점은 리스가 처음 만들어질 때만 쓰인다.
if os.environ.get("CosmosDBConnection__accountEndpoint"):
    _start_from = (datetime.now(UTC) - PRETRANSLATE_BACKFILL).strftime("%Y-%m-%dT%H:%M:%SZ")
    for _source in SOURCES:
        _register_pretranslate(_source, _start_from)

# Service Bus는 비용 때문에 평소 꺼 둔다(pause = 네임스페이스 삭제). 꺼져 있으면 트리거를 등록하지 않고
# handle_replay가 같은 처리를 직접 한다. 설정이 바뀌면 호스트가 재시작되며 다시 색인된다.
if os.environ.get("SERVICEBUS_FQDN"):

    @app.service_bus_topic_trigger(
        arg_name="msg",
        connection=SB_CONNECTION,
        topic_name=TOPIC_FLOW,
        subscription_name="early-warning",
        is_sessions_enabled=True,
    )
    def flow_updated(msg: func.ServiceBusMessage) -> None:
        handle_flow_update(deps(), msg.get_body().decode("utf-8"))

    @app.service_bus_topic_trigger(
        arg_name="msg",
        connection=SB_CONNECTION,
        topic_name=TOPIC_REPAYMENT,
        subscription_name="collections",
        is_sessions_enabled=True,
    )
    def repayment_updated(msg: func.ServiceBusMessage) -> None:
        handle_repayment_updated(deps(), msg.get_body().decode("utf-8"))
