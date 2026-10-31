"""Azure Functions 진입점(Python v2 모델). 업무 로직은 journey 패키지에 있다."""

import azure.durable_functions as df
import azure.functions as func

from journey.activities import ACTIVITIES, Deps
from journey.entry import handle_decision, handle_start
from journey.orchestration import journey

app = df.DFApp(http_auth_level=func.AuthLevel.FUNCTION)
RETRY = df.RetryOptions(first_retry_interval_in_milliseconds=5000, max_number_of_attempts=3)
_deps: Deps | None = None


def deps() -> Deps:
    global _deps
    if _deps is None:
        from journey.adapters import build_deps

        _deps = build_deps()
    return _deps


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
    return ACTIVITIES["request_consent"](deps(), payload)


@app.activity_trigger(input_name="payload")
def assemble_data(payload: dict) -> dict:
    return ACTIVITIES["assemble_data"](deps(), payload)


@app.activity_trigger(input_name="payload")
def underwrite(payload: dict) -> dict:
    return ACTIVITIES["underwrite"](deps(), payload)


@app.activity_trigger(input_name="payload")
def evaluate_policy(payload: dict) -> dict:
    return ACTIVITIES["evaluate_policy"](deps(), payload)
