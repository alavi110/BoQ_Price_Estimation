"""
LLM weight parser tests (US2).

The parser is the only place where a language model's free-form output becomes a
weight vector, so it is the only place where a bad answer can look like a good
one. Every number it returns feeds ``P = P_base * SUM(W_i * ratio_i)``: a
negative weight scales a price down, a weight of 2.0 doubles one component's
contribution, and a silently dropped entry redistributes its share across the
others. None of those raise; all of them produce a plausible number.

So the tests here treat the model as an untrusted input rather than a
collaborator. They cover three things the parser must guarantee:

* whatever comes back, the result is a valid, normalised, non-negative vector
  over the canonical seven components;
* when the answer cannot be trusted, the fallback says so - via a low confidence
  and a distinct model name - rather than passing off a keyword guess as an
  analysis;
* a successful call is actually recorded. Token usage reaching the audit trail
  is what makes an analysis run's cost accountable, and a hard-coded zero is
  indistinguishable from free.

No network call is made. ``LLMParser`` takes its integration by injection, so a
stub stands in for OpenRouter and the tests exercise the real prompt-building,
parsing and degradation paths.
"""
import json

import pytest

from src.integrations.openrouter import MODEL_CONFIGS, OpenRouterIntegration
from src.services.weight_attribution.llm_parser import (
    COMPONENTS,
    LLMParser,
    LLMWeightResult,
    parse_item_weights,
)

DESCRIPTION = "کابل تحت زمینی ۳x۱۵۰ میلی‌متر مربع مسی"


class StubIntegration:
    """Stands in for OpenRouter, recording what it was asked."""

    def __init__(self, content="", *, configured=True, raises=None, usage=None,
                 model="stub/model-1"):
        self._content = content
        self._configured = configured
        self._raises = raises
        self._usage = usage
        self._model = model
        self.calls = []

    @property
    def is_configured(self):
        return self._configured

    async def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        if isinstance(self._content, Exception):
            raise self._content
        return {
            "content": self._content,
            "model": self._model,
            "tokens_used": (self._usage or {}).get("total", 0),
            "prompt_tokens": (self._usage or {}).get("prompt", 0),
            "completion_tokens": (self._usage or {}).get("completion", 0),
        }


def reply(components, confidence=0.9, category="Electrical Cable", reasoning="ok"):
    """A well-formed model reply, so each test can spoil exactly one field."""
    return json.dumps({
        "category": category,
        "components": components,
        "confidence": confidence,
        "reasoning": reasoning,
    })


def parser_for(content="", **kwargs):
    return LLMParser(integration=StubIntegration(content, **kwargs))


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #
class TestSuccessfulAnalysis:
    async def test_the_weights_come_back(self):
        parser = parser_for(reply({"copper": 0.7, "polymer": 0.2, "labor": 0.1}))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.component_weights["copper"] == pytest.approx(0.70)
        assert result.component_weights["polymer"] == pytest.approx(0.20)
        assert result.component_weights["labor"] == pytest.approx(0.10)

    async def test_the_weights_sum_to_one(self):
        parser = parser_for(reply({"copper": 0.5, "steel": 0.3, "labor": 0.2}))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert sum(result.component_weights.values()) == pytest.approx(1.0, abs=1e-12)

    async def test_all_seven_components_are_reported(self):
        """
        Omitted components read as 0.0 rather than being absent.

        The document's table and the fusion both iterate the canonical set, so a
        missing key would surface as a blank cell or a KeyError rather than as
        "this item has no steel in it".
        """
        parser = parser_for(reply({"copper": 1.0}))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert set(result.component_weights) == set(COMPONENTS)
        assert len(COMPONENTS) == 7
        assert result.component_weights["steel"] == 0.0

    async def test_the_ones_the_model_omits_still_sum_to_one(self):
        """
        The completion case of the previous test: if the omitted entries were
        not in the divisor, three named components would total 1.0 and the
        vector as a whole would total 1.0 by accident, with the individual
        shares then inflating every price computed from them.
        """
        parser = parser_for(reply({"copper": 0.5, "steel": 0.3, "labor": 0.2}))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.component_weights["copper"] == pytest.approx(0.50)
        assert result.component_weights["overhead"] == 0.0

    async def test_weights_that_do_not_sum_to_one_are_normalised(self):
        """
        The model is asked for percentages summing to 100% and does not always
        comply. Renormalising is the difference between a slightly-off answer
        and a price scaled by whatever the model happened to produce.
        """
        parser = parser_for(reply({"copper": 70, "polymer": 20, "labor": 10}))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert sum(result.component_weights.values()) == pytest.approx(1.0, abs=1e-12)
        assert result.component_weights["copper"] == pytest.approx(0.70)

    async def test_the_category_and_reasoning_are_carried_through(self):
        """
        FR-028: the document explains the weights. A number with no stated
        reason is not defensible in a tender dispute.
        """
        parser = parser_for(reply(
            {"copper": 1.0},
            category="کابل برق",
            reasoning="conductor dominates the unit cost",
        ))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.category == "کابل برق"
        assert result.reasoning == "conductor dominates the unit cost"

    async def test_the_confidence_is_carried_through(self):
        parser = parser_for(reply({"copper": 1.0}, confidence=0.82))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.confidence == pytest.approx(0.82)

    async def test_the_real_token_count_is_recorded(self):
        """
        The audit trail's token figure is what an analysis run is costed
        against. A hard-coded zero is indistinguishable from a free call, so
        the count has to survive the whole path from the provider's reply.
        """
        parser = parser_for(
            reply({"copper": 1.0}),
            usage={"total": 1234, "prompt": 1100, "completion": 134},
        )

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.tokens_used == 1234

    async def test_the_model_that_answered_is_named(self):
        """
        The defence document attributes the analysis to a model. Reporting the
        configured default rather than the one that replied would misattribute
        it whenever the provider routes elsewhere.
        """
        parser = parser_for(reply({"copper": 1.0}), model="anthropic/actual-model")

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "anthropic/actual-model"


# --------------------------------------------------------------------------- #
# Untrusted output
# --------------------------------------------------------------------------- #
class TestHostileResponses:
    """Each of these produces a plausible wrong answer rather than an error."""

    async def test_a_negative_weight_is_refused_rather_than_clamped(self):
        """
        A material cannot be a negative share of a cost. Clamping to zero would
        quietly delete the entry and renormalise the rest, attributing a share
        the model never gave; passing it through would scale a price *down* by
        that share, which is a number no reviewer would question.
        """
        parser = parser_for(reply({"copper": -0.4, "steel": 1.4}))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "fallback"
        assert all(w >= 0 for w in result.component_weights.values())

    async def test_a_string_weight_is_refused_rather_than_raising(self):
        """
        A non-numeric weight raises inside the sum that would otherwise have
        produced a number, taking down the whole analysis run for one bad field.
        """
        parser = parser_for(reply({"copper": "0.7", "steel": 0.3}))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "fallback"
        assert sum(result.component_weights.values()) == pytest.approx(1.0, abs=1e-12)

    async def test_a_boolean_weight_is_refused(self):
        """
        ``True`` is an ``int`` in Python, so a naive check reads it as 1.0 - a
        component that claims the entire cost of the item because the model
        wrote ``true``.
        """
        parser = parser_for(reply({"copper": True, "steel": False}))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "fallback"

    async def test_a_null_weight_is_refused(self):
        parser = parser_for(reply({"copper": None}))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "fallback"

    async def test_all_zero_weights_are_refused(self):
        """
        A vector of zeros has no cost structure to read. Normalising it would
        divide by zero; leaving it would hand the fusion a vector summing to 0,
        which its own normalisation turns into an equal split - presented as if
        the model had said so.
        """
        parser = parser_for(reply({"copper": 0, "steel": 0}))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "fallback"
        assert sum(result.component_weights.values()) == pytest.approx(1.0, abs=1e-12)

    async def test_a_missing_components_block_is_refused(self):
        parser = parser_for(reply({"copper": 1.0}).replace('"components"', '"comps"'))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "fallback"

    async def test_an_unknown_component_key_is_dropped_and_the_rest_rescaled(self):
        """
        The model was asked for exactly seven keys and invented an eighth.

        Its seven stated shares are kept and renormalised over each other, which
        gives what the model would have returned had it not invented ``glass``.
        Keeping the key instead would push the known shares below 1.0 and imply a
        share to a component the price formula has no index for; rejecting the
        whole answer would discard a usable attribution over one stray key.
        """
        parser = parser_for(reply({"copper": 0.5, "steel": 0.3, "glass": 0.2}))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert set(result.component_weights) == set(COMPONENTS)
        assert "glass" not in result.component_weights
        # 0.5 / 0.8, not 0.5.
        assert result.component_weights["copper"] == pytest.approx(0.625)
        assert result.component_weights["steel"] == pytest.approx(0.375)
        assert sum(result.component_weights.values()) == pytest.approx(1.0, abs=1e-12)

    async def test_a_json_array_is_refused(self):
        """
        Valid JSON, wrong shape. A model asked for an object sometimes returns
        the components on their own, which is a different answer, not the
        answer that was requested.
        """
        parser = parser_for('[{"copper": 0.7}]')

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "fallback"

    async def test_prose_instead_of_json_is_refused(self):
        parser = parser_for("The item is mostly copper, about 70 percent.")

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "fallback"
        assert result.component_weights["copper"] > 0  # from the keyword path

    async def test_an_empty_reply_is_refused(self):
        parser = parser_for("")

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "fallback"

    @pytest.mark.parametrize("raw", [0.95, 1.5, True, None, "high", {"a": 1}, []])
    async def test_a_confidence_of_any_shape_is_clamped_or_defaulted(self, raw):
        """
        Confidence is a claim about how far to trust the answer, so it is
        clamped rather than propagated: an unclamped 1.5 would enter the fusion
        as more certainty than the model asserted.
        """
        parser = parser_for(reply({"copper": 1.0}, confidence=raw))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert 0.0 <= result.confidence <= 1.0

    async def test_a_confidence_above_one_is_clamped_to_one(self):
        parser = parser_for(reply({"copper": 1.0}, confidence=1.5))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.confidence == 1.0

    async def test_a_negative_confidence_is_clamped_to_zero(self):
        parser = parser_for(reply({"copper": 1.0}, confidence=-3.0))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.confidence == 0.0

    async def test_a_non_numeric_confidence_becomes_the_neutral_default(self):
        parser = parser_for(reply({"copper": 1.0}, confidence="confident"))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.confidence == pytest.approx(0.5)

    async def test_a_missing_category_becomes_a_known_placeholder(self):
        parser = parser_for(reply({"copper": 1.0}).replace('"category"', '"kind"'))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.category == "Unknown"

    async def test_a_non_string_category_is_coerced(self):
        parser = parser_for(reply({"copper": 1.0}, category=17))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.category == "17"


# --------------------------------------------------------------------------- #
# Degradation
# --------------------------------------------------------------------------- #
class TestFallback:
    async def test_no_api_key_means_the_fallback_without_a_request(self):
        """
        An unconfigured deployment is a state to handle, not an error to raise.
        Checking it up front also means a missing key cannot present as a
        mysterious provider failure later.
        """
        integration = StubIntegration(reply({"copper": 1.0}), configured=False)
        parser = LLMParser(integration=integration)

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "fallback"
        assert integration.calls == []

    async def test_a_provider_failure_degrades_instead_of_raising(self):
        """
        The caller has one useful alternative to a real answer - a disclosed
        keyword guess - and raising would give it nothing.
        """
        parser = parser_for(raises=RuntimeError("openrouter 503"))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "fallback"
        assert sum(result.component_weights.values()) == pytest.approx(1.0, abs=1e-12)

    async def test_a_transport_failure_degrades_too(self):
        parser = parser_for(raises=OSError("connection reset"))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.model_used == "fallback"

    async def test_the_fallback_declares_its_own_low_confidence(self):
        """
        Load-bearing. This number is what tells the fusion to lean on the ML
        side and the document to show where the weights came from. A fallback
        reporting high confidence would be an indistinguishable lie.
        """
        parser = parser_for(raises=RuntimeError("down"))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.confidence == pytest.approx(0.3)

    async def test_the_fallback_explains_itself(self):
        parser = parser_for(raises=RuntimeError("down"))

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert "allback" in result.reasoning
        assert "allback" in result.category

    async def test_a_degraded_call_records_no_tokens(self):
        """
        No call was made, so no tokens were spent. Recording a non-zero figure
        here would corrupt the cost accounting the successful path maintains.
        """
        parser = parser_for(raises=RuntimeError("down"), usage={"total": 999})

        result = await parser.analyze_item("1.1", DESCRIPTION)

        assert result.tokens_used == 0

    @pytest.mark.parametrize("description, expected_heavy", [
        ("کابل مسی ۳x۱۵۰", "copper"),
        ("بتن آرمه کلاس ۳۵۰", "cement"),
        ("میلگرد آرماتور فولادی", "steel"),
        ("لوله پلی اتیلن ۱۱۰", "polymer"),
        ("عملیات حفاری خاک", "energy"),
    ])
    async def test_each_keyword_family_leads_with_its_own_material(
        self, description, expected_heavy
    ):
        """
        The keyword path is what runs whenever the model is unavailable, so it
        has to be directionally right on its own.

        Only the leading component is asserted: the mixes are crude by design,
        and this file is not the place to pretend otherwise.
        """
        parser = parser_for(raises=RuntimeError("down"))

        result = await parser.analyze_item("1.1", description)

        heaviest = max(result.component_weights, key=result.component_weights.get)
        assert heaviest == expected_heavy

    async def test_an_unrecognised_description_still_yields_a_valid_vector(self):
        """
        Better a disclosed equal-ish split than nothing: the price still
        computes, and the 0.3 confidence says it is a guess.
        """
        parser = parser_for(raises=RuntimeError("down"))

        result = await parser.analyze_item("1.1", "zzz qqq")

        assert set(result.component_weights) == set(COMPONENTS)
        assert sum(result.component_weights.values()) == pytest.approx(1.0, abs=1e-12)
        assert result.component_weights["overhead"] > 0

    async def test_a_missing_description_does_not_crash_the_fallback(self):
        """
        A BoQ row with no description is a real possibility, and ``None.lower()``
        would take down the run for it.
        """
        parser = parser_for(raises=RuntimeError("down"))

        result = await parser.analyze_item("1.1", None)

        assert sum(result.component_weights.values()) == pytest.approx(1.0, abs=1e-12)

    async def test_every_fallback_vector_is_a_valid_distribution(self):
        """
        Swept rather than sampled: the keyword table is the part of this service
        most likely to be edited, and a typo in it would produce a vector the
        price formula multiplies straight through.
        """
        parser = parser_for(raises=RuntimeError("down"))
        for description in ["", "مسی", "فولاد", "بتن", "لوله", "حفاری", "ژنرال", "۱۲۳۴۵"]:
            result = await parser.analyze_item("1.1", description)
            assert sum(result.component_weights.values()) == pytest.approx(1.0, abs=1e-12)
            assert all(w >= 0 for w in result.component_weights.values())


# --------------------------------------------------------------------------- #
# The prompt
# --------------------------------------------------------------------------- #
class TestPromptConstruction:
    async def test_the_system_prompt_is_sent_as_the_system_message(self):
        """
        As a role, not inlined into the user turn. Sending it both ways doubled
        the input tokens of every call for no change in the instructions.
        """
        integration = StubIntegration(reply({"copper": 1.0}))
        parser = LLMParser(integration=integration)

        await parser.analyze_item("1.1", DESCRIPTION)

        messages = integration.calls[0]["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == LLMParser.SYSTEM_PROMPT

    async def test_the_system_prompt_is_not_repeated_in_the_user_turn(self):
        integration = StubIntegration(reply({"copper": 1.0}))
        parser = LLMParser(integration=integration)

        await parser.analyze_item("1.1", DESCRIPTION)

        user_turn = integration.calls[0]["messages"][1]["content"]
        assert "You are an expert cost estimator" not in user_turn

    async def test_the_few_shot_examples_are_included(self):
        """
        They are the whole demonstration budget, and an unrecognised item's
        component mix tends to copy whichever example it resembles most.
        """
        integration = StubIntegration(reply({"copper": 1.0}))
        parser = LLMParser(integration=integration)

        await parser.analyze_item("1.1", DESCRIPTION)

        assert "Examples:" in integration.calls[0]["messages"][1]["content"]

    async def test_the_examples_carry_no_latin_letters(self):
        """
        Two of the shipped examples had Latin fragments spliced into the middle
        of Persian words - "بتنی آر braço" and "حفاری mekanیک".

        Sent as few-shot demonstrations, that teaches the model to emit
        mixed-script text for Persian BoQ descriptions, and a mixed-script
        description matches none of the fallback's keywords either. Checked for
        Latin *letters* specifically, since a Latin "x" used as a multiplication
        sign in "۳x۱۵۰" is deliberate and correct.
        """
        import re

        for line in LLMParser.FEW_SHOT_EXAMPLES.splitlines():
            if 'Item: "' not in line:
                continue
            described = line.split('Item: "', 1)[1].rsplit('"', 1)[0]
            assert not re.search(r"[A-Za-z]", described), described

    async def test_the_item_code_and_unit_reach_the_model(self):
        integration = StubIntegration(reply({"copper": 1.0}))
        parser = LLMParser(integration=integration)

        await parser.analyze_item("3.7.2", DESCRIPTION, unit="متر")

        user_turn = integration.calls[0]["messages"][1]["content"]
        assert "3.7.2" in user_turn
        assert "متر" in user_turn
        assert DESCRIPTION in user_turn

    async def test_project_context_is_included_as_json(self):
        """
        Section context changes the answer materially - a cable in a coastal
        substation is not the same item as one inland - so it is passed through.
        """
        integration = StubIntegration(reply({"copper": 1.0}))
        parser = LLMParser(integration=integration)

        await parser.analyze_item(
            "1.1", DESCRIPTION, project_context={"region": "coastal", "voltage_kv": 33}
        )

        user_turn = integration.calls[0]["messages"][1]["content"]
        assert "coastal" in user_turn
        assert "33" in user_turn

    async def test_absent_optional_context_is_simply_omitted(self):
        integration = StubIntegration(reply({"copper": 1.0}))
        parser = LLMParser(integration=integration)

        await parser.analyze_item("1.1", DESCRIPTION)

        user_turn = integration.calls[0]["messages"][1]["content"]
        assert "Project context" not in user_turn
        assert "English description" not in user_turn

    async def test_an_english_description_is_included_when_present(self):
        integration = StubIntegration(reply({"copper": 1.0}))
        parser = LLMParser(integration=integration)

        await parser.analyze_item("1.1", DESCRIPTION, description_en="MV copper cable")

        assert "MV copper cable" in \
            integration.calls[0]["messages"][1]["content"]

    async def test_json_output_is_requested(self):
        """
        Free-form output would land in the fallback path for every item, which
        is the same as having no integration.
        """
        integration = StubIntegration(reply({"copper": 1.0}))
        parser = LLMParser(integration=integration)

        await parser.analyze_item("1.1", DESCRIPTION)

        assert integration.calls[0]["response_format"] == {"type": "json_object"}

    async def test_a_low_temperature_is_requested(self):
        """
        This is a costing input. Run-to-run variation in a weight vector becomes
        run-to-run variation in a tender price, and a defence document has to
        reproduce the number it was issued for.
        """
        integration = StubIntegration(reply({"copper": 1.0}))
        parser = LLMParser(integration=integration)

        await parser.analyze_item("1.1", DESCRIPTION)

        assert integration.calls[0]["temperature"] <= 0.2

    async def test_a_output_token_budget_is_set(self):
        """
        The reply is a small JSON object. The budget stops a long description
        from turning into an unbounded bill on a per-item loop.
        """
        integration = StubIntegration(reply({"copper": 1.0}))
        parser = LLMParser(integration=integration)

        await parser.analyze_item("1.1", DESCRIPTION)

        assert 0 < integration.calls[0]["max_tokens"] <= 1000

    async def test_a_degraded_call_makes_exactly_one_request(self):
        """
        No silent retry. A retry loop against a failing provider multiplies the
        cost of an outage by the retry count while the user waits.
        """
        integration = StubIntegration(raises=RuntimeError("503"))
        parser = LLMParser(integration=integration)

        await parser.analyze_item("1.1", DESCRIPTION)

        assert len(integration.calls) == 1


# --------------------------------------------------------------------------- #
# The transport the parser depends on
# --------------------------------------------------------------------------- #
class TestOpenRouterTransport:
    def test_no_api_key_is_reported_as_unconfigured(self, monkeypatch):
        """
        A deployment state, surfaced as a boolean. Callers branch on it and take
        the documented fallback path, rather than discovering it as a failed
        request against a live provider.
        """
        from src.core import config as config_module

        monkeypatch.setattr(config_module.settings, "OPENROUTER_API_KEY", "")
        integration = OpenRouterIntegration()

        assert integration.is_configured is False

    async def test_a_chat_completion_without_a_key_is_refused(self, monkeypatch):
        """
        Refused before the request, so a missing deployment setting does not
        present as a provider outage.
        """
        from src.core import config as config_module

        monkeypatch.setattr(config_module.settings, "OPENROUTER_API_KEY", "")
        integration = OpenRouterIntegration()

        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
            await integration.chat_completion(messages=[{"role": "user", "content": "x"}])

    def test_the_transport_is_not_a_missing_sdk(self):
        """
        The client is an ``httpx.AsyncClient`` built at construction.

        It used to be ``from openrouter import OpenRouter`` inside a
        ``try``/``except ImportError``, which always raised and set the client
        to ``None`` - so every LLM call silently took the keyword fallback while
        the logs reported a successful analysis. OpenRouter's REST endpoint is
        reached directly instead, over a dependency that is actually installed.
        """
        import httpx

        integration = OpenRouterIntegration()

        assert isinstance(integration.client, httpx.AsyncClient)
        assert str(integration.client.base_url).rstrip("/").endswith("/api/v1")

    def test_requests_carry_project_attribution_headers(self):
        """
        OpenRouter attributes traffic with these. Without them a provider
        account shows anonymous requests, which cannot be reconciled against the
        per-item token usage this service records.
        """
        integration = OpenRouterIntegration()

        assert integration._headers()["HTTP-Referer"]
        assert integration._headers()["X-Title"]

    @pytest.mark.parametrize("environment", ["development", "production", "local"])
    def test_every_environment_offers_models(self, environment):
        assert OpenRouterIntegration().get_available_models(environment)

    def test_an_unknown_environment_falls_back_to_development(self):
        """
        An unrecognised ENVIRONMENT must not produce an empty catalogue, which
        would leave the caller with no model to choose and no error explaining
        why.
        """
        models = OpenRouterIntegration().get_available_models("staging")

        assert models == MODEL_CONFIGS["development"]

    def test_a_known_model_can_be_selected(self):
        integration = OpenRouterIntegration()

        assert integration.set_model("openai/gpt-4o") is True
        assert integration.get_current_model() == "openai/gpt-4o"

    def test_a_model_outside_the_catalogue_is_refused(self):
        """
        A typo in a model id would otherwise be accepted and fail as a provider
        error on the next call, one request at a time.
        """
        integration = OpenRouterIntegration()

        assert integration.set_model("gpt-40") is False

    def test_any_openrouter_auto_model_is_accepted(self):
        """
        ``openrouter/auto`` resolves server-side to whatever is available, so
        there is no way to enumerate its targets - and the default model is one
        of them.
        """
        integration = OpenRouterIntegration()

        assert integration.set_model("openrouter/auto") is True
        assert integration.set_model("openrouter/some-new-model") is True

    def test_cost_is_zero_for_a_free_model(self):
        integration = OpenRouterIntegration()

        assert integration.estimate_cost(1000, 1000, "openrouter/auto") == 0.0

    def test_cost_is_estimated_for_a_paid_model(self):
        """
        What lets an analysis run's LLM spend be reported alongside the price it
        produced.
        """
        integration = OpenRouterIntegration()

        cost = integration.estimate_cost(1000, 1000, "openai/gpt-4o")

        assert cost == pytest.approx(5.0 + 15.0)

    def test_an_unknown_model_costs_nothing_rather_than_guessing(self):
        """
        Zero is the honest answer for a model whose rates are not configured. A
        fabricated figure would put a wrong number in a cost report.
        """
        assert OpenRouterIntegration().estimate_cost(1000, 1000, "who/knows") == 0.0


# --------------------------------------------------------------------------- #
# The convenience wrapper
# --------------------------------------------------------------------------- #
class TestModuleHelper:
    async def test_the_helper_returns_a_result(self):
        """Constructs its own parser, so it takes the deployment's config."""
        result = await parse_item_weights("1.1", DESCRIPTION)

        assert isinstance(result, LLMWeightResult)
        assert sum(result.component_weights.values()) == pytest.approx(1.0, abs=1e-12)

    async def test_the_helper_agrees_with_a_fallback_parser(self):
        helper = await parse_item_weights("1.1", DESCRIPTION)
        direct = await parser_for(raises=RuntimeError("down")).analyze_item("1.1", DESCRIPTION)

        assert helper.component_weights == direct.component_weights
