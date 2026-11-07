"""Azure Functions 진입점(Python v2 모델). 업무 로직은 journey 패키지에 있다."""

import os

import azure.durable_functions as df
import azure.functions as func

from journey import telemetry
from journey.activities import ACTIVITIES, Deps
from journey.consent import CONSENT_ACTIVITIES, handle_consent_command, handle_lifecycle_start
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

telemetry.setup()

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
    await handle_lifecycle_start(client, msg.get_body().decode("utf-8"))


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
    handle_replay(deps(), msg.get_body().decode("utf-8"))


@app.queue_trigger(arg_name="msg", queue_name="restructure-decisions", connection="AzureWebJobsStorage")
def restructure_decision(msg: func.QueueMessage) -> None:
    handle_restructure_decision(deps(), msg.get_body().decode("utf-8"))


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
