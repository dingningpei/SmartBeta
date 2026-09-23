"""Pilot-1A P1A-G3: provider-neutral model adapter + deterministic stub.

This module owns **only** the provider-neutral model adapter described by
``worker_tasks/pilot1/pilot1-plan.md`` section 11 (the P1A-G3 row of section
18's task table) plus the deterministic in-process ``StubModelClient`` used by
every Pilot-1A test, barrier and dry run.

Binding user freeze 2 (plan section 25, harness record)
--------------------------------------------------------

**No real model-provider SDK may be added during P1A-G3..H6.** ``ModelClient``
stays a provider-neutral protocol from
:mod:`smart_beta.pilot.contracts`; the only implementation here is the
deterministic :class:`StubModelClient`. There is no credential read, no real
model invocation and no external network access. The concrete provider
adapter is deferred until after barrier H6 and needs separate authorization.

Frozen flow per invocation (plan section 11)
--------------------------------------------

1. Render the frozen template from the two allowlisted objects'
   ``to_dict()`` + fixed template text + the closed raw JSON candidate schema
   (:func:`smart_beta.pilot.prompt.render_request`).
2. Run the **pre-call firewall audit** over the structured inputs
   (:func:`smart_beta.pilot.firewall.audit_generator_inputs`). On failure the
   typed ``HOLDOUT_FIREWALL_VIOLATION`` path is raised and **no** call is
   made.
3. Append the :class:`~smart_beta.pilot.contracts.InvocationIntent` to the
   injected :class:`~smart_beta.pilot.contracts.JournalSink` and flush it
   **durably** before the external call.
4. Call :meth:`ModelClient.complete`.
5. Append the :class:`~smart_beta.pilot.contracts.InvocationResult` and flush
   it durably.
6. Return :class:`~smart_beta.research.loop.GenerationOutput`, or raise
   :class:`~smart_beta.research.loop.GeneratorFailureError` on refusal, empty
   output or a transport error. Transport errors are retried **only** when
   the frozen configuration allows it, and every retry is journaled.

Write-ahead invariant
---------------------

The durable intent is written **before** ``complete``; a crash inside the
call therefore leaves an intent without a result, which the reconstruction
verifier reports as an interrupted invocation. The adapter never reorders
these two appends.

Trust boundary
--------------

The adapter reads no data credential and logs no model credential. It passes
nothing except the two allowlisted objects and fixed template text. It
imports the read-only Phase-6/9 contracts plus P1A-C/G3 only, performs no
network access and never calls ``eval``/``exec``/``subprocess``.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from smart_beta.pilot.contracts import (
    GENESIS_PREV_SHA256,
    InvocationErrorState,
    InvocationIntent,
    InvocationResult,
    JournalKind,
    JournalRecord,
    JournalSink,
    ModelClient,
    ModelRequest,
    ModelResponse,
    validate_run_id,
)
from smart_beta.pilot.firewall import audit_generator_inputs
from smart_beta.pilot.prompt import (
    PROMPT_TEMPLATE_TEXT,
    RenderedRequest,
    render_request,
)
from smart_beta.research.generator import RawArtifact
from smart_beta.research.history import (
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)
from smart_beta.research.loop import GenerationOutput, GeneratorFailureError
from smart_beta.research.policy import ResearchPolicy

__all__ = [
    "ModelAdapterError",
    "ModelTransportError",
    "PriceTable",
    "StubModelClient",
    "JournalChain",
    "ModelAdapter",
    "utc_now_iso",
]


class ModelAdapterError(ValueError):
    """A Pilot-1A model-adapter contract is malformed (fail closed)."""


class ModelTransportError(Exception):
    """A transport-level failure raised by a ``ModelClient`` before a response.

    The adapter maps this to the typed
    :class:`~smart_beta.research.loop.GeneratorFailureError` path (after
    journaling a result and, when allowed, one journaled retry).
    """


def utc_now_iso() -> str:
    """The default metadata clock (wall clock; never part of an identity)."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PriceTable:
    """The frozen per-token price table used for cost accounting.

    Prices are in currency units per single token and must be finite and
    non-negative. The table is metadata for the run manifest; it never enters
    any identity hash.
    """

    input_per_token: float
    output_per_token: float

    def __post_init__(self) -> None:
        for name in ("input_per_token", "output_per_token"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ModelAdapterError(
                    f"{name} must be a finite number, got {type(value).__name__}"
                )
            number = float(value)
            if not math.isfinite(number) or number < 0.0:
                raise ModelAdapterError(
                    f"{name} must be finite and non-negative, got {value!r}"
                )

    def cost_for(self, input_tokens: int, output_tokens: int) -> float:
        """The computed cost of an invocation from its token usage."""
        return (
            int(input_tokens) * float(self.input_per_token)
            + int(output_tokens) * float(self.output_per_token)
        )


class StubModelClient:
    """A deterministic, offline :class:`ModelClient` (no provider SDK).

    Responses are consumed in order: each call returns ``responses[index]``
    (or raises it, if the entry is a :class:`BaseException`). The client never
    reads a credential, never touches the network and never varies between
    runs. ``before_call`` is an optional hook the tests use to assert the
    durable write-ahead intent exists at the moment of the call.
    """

    def __init__(
        self,
        responses: ModelResponse | BaseException | Sequence[ModelResponse | BaseException],
        *,
        before_call: Callable[[ModelRequest, int], None] | None = None,
    ) -> None:
        if isinstance(responses, (ModelResponse, BaseException)):
            normalized: list[ModelResponse | BaseException] = [responses]
        elif isinstance(responses, Sequence) and not isinstance(
            responses, (str, bytes)
        ):
            normalized = list(responses)
        else:
            raise ModelAdapterError(
                "responses must be a ModelResponse/exception or a sequence of them"
            )
        if not normalized:
            raise ModelAdapterError("responses must not be empty")
        self._responses = normalized
        self._before_call = before_call
        self._calls: list[ModelRequest] = []

    @property
    def calls(self) -> tuple[ModelRequest, ...]:
        """Every request the stub received, in call order."""
        return tuple(self._calls)

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return the next scripted response (deterministic, single turn)."""
        if not isinstance(request, ModelRequest):
            raise ModelTransportError(
                f"stub received a non-ModelRequest: {type(request).__name__}"
            )
        index = len(self._calls)
        self._calls.append(request)
        if self._before_call is not None:
            self._before_call(request, index)
        if index >= len(self._responses):
            raise ModelTransportError(
                "stub response sequence exhausted; no hidden fallback response"
            )
        item = self._responses[index]
        if isinstance(item, BaseException):
            raise item
        return item


class JournalChain:
    """Shared append-only hash-chain state for :class:`JournalRecord` builds.

    The P1A-C :class:`~smart_beta.pilot.contracts.JournalSink` accepts a fully
    formed :class:`~smart_beta.pilot.contracts.JournalRecord` envelope
    (``seq``/``prev_sha256``). Because a single run journal is written by more
    than one harness component, the chain state must be **shared** rather than
    owned by the adapter. P1A-G5 constructs one of these, shares it with the
    runner, and passes it to :class:`ModelAdapter`; the P1A-G3 tests construct
    a fresh one over an in-memory sink.
    """

    def __init__(
        self,
        run_id: str,
        *,
        next_seq: int = 0,
        prev_sha256: str = GENESIS_PREV_SHA256,
    ) -> None:
        self._run_id = validate_run_id(run_id)
        if isinstance(next_seq, bool) or not isinstance(next_seq, int) or next_seq < 0:
            raise ModelAdapterError("next_seq must be a non-negative integer")
        if not isinstance(prev_sha256, str) or len(prev_sha256) != 64:
            raise ModelAdapterError("prev_sha256 must be a 64-char hex SHA-256")
        self._next_seq = next_seq
        self._prev_sha256 = prev_sha256

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def next_seq(self) -> int:
        return self._next_seq

    @property
    def tip(self) -> str:
        """The ``prev_sha256`` the next record must carry."""
        return self._prev_sha256

    def build(self, kind: JournalKind, payload: Mapping[str, Any]) -> JournalRecord:
        """Build the next chained record and advance the chain tip."""
        record = JournalRecord.create(
            seq=self._next_seq,
            run_id=self._run_id,
            kind=kind,
            payload=payload,
            prev_sha256=self._prev_sha256,
        )
        self._next_seq += 1
        self._prev_sha256 = record.chain_hash()
        return record


class ModelAdapter:
    """The provider-neutral generator callable over a ``ModelClient``.

    An instance is callable as
    ``adapter(GeneratorVisibleResearchHistory, ResearchFeedback)`` and returns
    a :class:`~smart_beta.research.loop.GenerationOutput`, so it can be handed
    directly to :meth:`smart_beta.research.loop.ResearchLoop.generate` as the
    sealed generator callable. It performs the frozen render / firewall /
    write-ahead-intent / call / result flow and never claims determinism.
    """

    def __init__(
        self,
        *,
        run_id: str,
        research_policy: ResearchPolicy,
        client: ModelClient,
        journal: JournalSink,
        model_provider: str,
        model_id: str,
        price_table: PriceTable,
        settings: Any = (),
        system: str | None = None,
        template: str = PROMPT_TEMPLATE_TEXT,
        max_provider_retries: int = 0,
        clock: Callable[[], str] = utc_now_iso,
        chain: JournalChain | None = None,
    ) -> None:
        self._run_id = validate_run_id(run_id)
        if chain is None:
            chain = JournalChain(self._run_id)
        if not isinstance(research_policy, ResearchPolicy):
            raise ModelAdapterError(
                "research_policy must be a ResearchPolicy, got "
                f"{type(research_policy).__name__}"
            )
        if not isinstance(chain, JournalChain):
            raise ModelAdapterError(
                f"chain must be a JournalChain, got {type(chain).__name__}"
            )
        if chain.run_id != self._run_id:
            raise ModelAdapterError(
                "journal chain run_id does not match the adapter run_id"
            )
        if not isinstance(price_table, PriceTable):
            raise ModelAdapterError("price_table must be a PriceTable")
        if not isinstance(model_provider, str) or not model_provider.strip():
            raise ModelAdapterError("model_provider must be non-empty text")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ModelAdapterError("model_id must be non-empty text")
        if isinstance(max_provider_retries, bool) or not isinstance(
            max_provider_retries, int
        ):
            raise ModelAdapterError("max_provider_retries must be an integer")
        if max_provider_retries < 0:
            raise ModelAdapterError("max_provider_retries must be non-negative")
        if not callable(clock):
            raise ModelAdapterError("clock must be callable")
        if not hasattr(client, "complete") or not callable(client.complete):
            raise ModelAdapterError("client must implement ModelClient.complete")

        self._policy_hash = research_policy.content_hash
        self._client = client
        self._journal = journal
        self._chain = chain
        self._model_provider = model_provider
        self._model_id = model_id
        self._price_table = price_table
        self._settings = settings
        self._system = system
        self._template = template
        self._template_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()
        self._max_provider_retries = max_provider_retries
        self._clock = clock
        self._ordinal = 0

    # -- read-only views -------------------------------------------------
    @property
    def next_ordinal(self) -> int:
        """The ordinal the next provider call will use."""
        return self._ordinal

    @property
    def prompt_template_hash(self) -> str:
        return self._template_hash

    # -- write-ahead helpers ---------------------------------------------
    def _append(self, kind: JournalKind, payload: Mapping[str, Any]) -> JournalRecord:
        record = self._chain.build(kind, payload)
        self._journal.append(record)
        self._journal.flush_durable()
        return record

    def _build_intent(self, rendered: RenderedRequest) -> InvocationIntent:
        ordinal = self._ordinal
        intent = InvocationIntent.create(
            run_id=self._run_id,
            ordinal=ordinal,
            model_provider=self._model_provider,
            model_id=self._model_id,
            settings=self._settings,
            prompt_template_hash=rendered.prompt_template_hash,
            visible_history_hash=rendered.visible_history_hash,
            research_feedback_hash=rendered.research_feedback_hash,
            research_policy_hash=self._policy_hash,
            request_artifact_hash=rendered.content_hash,
            created_at=self._clock(),
        )
        self._ordinal += 1
        return intent

    def _build_request(self, rendered: RenderedRequest) -> ModelRequest:
        request = ModelRequest(
            model_id=self._model_id,
            prompt=rendered.prompt,
            settings=self._settings,
            system=self._system,
            response_format="json",
        )
        payload = request.to_dict()
        for forbidden in ("tools", "web_search", "file_inputs", "server_tools"):
            if forbidden in payload:
                raise ModelAdapterError(
                    f"model request must not declare {forbidden!r}"
                )
        return request

    def _build_result(
        self,
        intent: InvocationIntent,
        response: ModelResponse | None,
        error_state: InvocationErrorState,
    ) -> InvocationResult:
        text = "" if response is None else response.text
        input_tokens = 0 if response is None else response.input_tokens
        output_tokens = 0 if response is None else response.output_tokens
        return InvocationResult.create(
            run_id=self._run_id,
            invocation_id=intent.invocation_id,
            ordinal=intent.ordinal,
            raw_response_text=text,
            response_model_id=None if response is None else response.model_id,
            stop_reason=None if response is None else response.stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=self._price_table.cost_for(input_tokens, output_tokens),
            error_state=error_state,
            received_at=self._clock(),
        )

    def _generation_output(
        self, response: ModelResponse, result: InvocationResult
    ) -> GenerationOutput:
        try:
            raw_artifact = RawArtifact.from_content(response.text)
        except ValueError as exc:  # includes RawArtifactSecretError
            raise GeneratorFailureError(
                "generator output is not an admissible raw artifact: "
                f"{type(exc).__name__}"
            ) from exc
        return GenerationOutput(
            raw_artifact=raw_artifact,
            tokens_used=response.input_tokens + response.output_tokens,
            cost_used=result.cost,
        )

    # -- the generator callable ------------------------------------------
    def __call__(
        self,
        visible: GeneratorVisibleResearchHistory,
        feedback: ResearchFeedback,
    ) -> GenerationOutput:
        """Run one fully journaled, firewalled model invocation."""
        rendered = render_request(visible, feedback, template=self._template)
        # Step 2: the pre-call firewall audit. On failure the typed
        # HOLDOUT_FIREWALL_VIOLATION path is raised before any intent or call.
        audit_generator_inputs(visible, feedback, request_payload=rendered.payload)

        transport_retries = 0
        while True:
            # Steps 3: write-ahead intent, flushed durably BEFORE the call.
            intent = self._build_intent(rendered)
            self._append(JournalKind.INVOCATION_INTENT, intent.to_dict())

            request = self._build_request(rendered)
            response: ModelResponse | None = None
            error_state = InvocationErrorState.NONE
            transport_error: ModelTransportError | None = None
            try:
                response = self._client.complete(request)
            except ModelTransportError as exc:
                error_state = InvocationErrorState.TRANSPORT_ERROR
                transport_error = exc

            if response is not None:
                if response.error_state is not InvocationErrorState.NONE:
                    error_state = response.error_state
                elif not response.text:
                    error_state = InvocationErrorState.EMPTY_OUTPUT

            # Step 5: result, flushed durably (including failure results).
            result = self._build_result(intent, response, error_state)
            self._append(JournalKind.INVOCATION_RESULT, result.to_dict())

            if error_state is InvocationErrorState.NONE:
                assert response is not None  # noqa: S101 - error_state implies it
                return self._generation_output(response, result)

            if (
                error_state is InvocationErrorState.TRANSPORT_ERROR
                and transport_retries < self._max_provider_retries
            ):
                transport_retries += 1
                continue

            cause = transport_error if transport_error is not None else None
            raise GeneratorFailureError(
                "model invocation failed with "
                f"{error_state.value!r} (invocation {intent.invocation_id})"
            ) from cause
