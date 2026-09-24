"""Evaluation test-template loading, rendering, and response extraction.

Test templates describe benchmark messages and answer structure.  They are
deliberately separate from a model/tokenizer chat template: this module never
adds BOS/EOS tokens or renders provider-specific special tokens.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any, Literal

from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict, Field, model_validator

BUILTIN_TEST_TEMPLATE_DIR = Path(__file__).with_name("conf") / "test_templates"
BUILTIN_TEST_TEMPLATES = {
    "lean-cot-v1": BUILTIN_TEST_TEMPLATE_DIR / "lean-cot-v1.yaml",
    "lean-plan-repair-v3": BUILTIN_TEST_TEMPLATE_DIR / "lean-plan-repair-v3.yaml",
}


class TestTemplateError(ValueError):
    """Raised when a test template or a rendered model response is invalid."""

    __test__ = False


class FieldMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    required: bool = True
    default: Any = ""
    transforms: list[Literal["strip", "rstrip", "json", "lean_comment"]] = Field(
        default_factory=list
    )


class MessageTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class ExtractorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "section_fenced_code",
        "section_fenced_code_relaxed",
        "section_raw_lean",
        "last_fenced_code",
        "raw_lean",
    ]
    name: str | None = None
    heading: str | None = None
    required_headings: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=lambda: ["lean4"])
    require_terminal: bool = False
    case_sensitive: bool = True

    @model_validator(mode="after")
    def validate_extractor(self) -> ExtractorSettings:
        if self.type.startswith("section_") and not self.heading:
            raise ValueError(f"{self.type} requires `heading`")
        if "fenced_code" in self.type and not self.languages:
            raise ValueError(f"{self.type} requires at least one language")
        return self


class OutputSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: ExtractorSettings
    repair_primary: ExtractorSettings | None = None
    fallbacks: list[ExtractorSettings] = Field(default_factory=list)


class TestTemplateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1, le=1)
    id: str = Field(min_length=1)
    fields: dict[str, FieldMapping]
    messages: list[MessageTemplate] = Field(min_length=1)
    output: OutputSettings


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    code: str
    strategy: str
    used_fallback: bool


_FENCE_RE = re.compile(
    r"^[ \t]*```(?P<language>[^\r\n`]*)[ \t]*\r?\n"
    r"(?P<code>.*?)"
    r"(?:\r?\n)?^[ \t]*```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
_LOOSE_FENCE_RE = re.compile(
    r"```[ \t]*(?P<language>[^\r\n`]*)[ \t]*\r?\n(?P<code>.*?)```",
    re.DOTALL,
)
_LEAN_START_RE = re.compile(
    r"\A\s*(?:(?:import|prelude|namespace|open|set_option|universe|variable|"
    r"theorem|lemma|example|def|instance|structure|class|inductive)\b|/-)",
    re.IGNORECASE,
)


def _resolve_source(source: str, record: Any) -> Any:
    current = record
    for part in source.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise KeyError(source)
            current = current[part]
        else:
            try:
                current = getattr(current, part)
            except AttributeError as exc:
                raise KeyError(source) from exc
    return current


def _lean_comment(value: str) -> str:
    lines = value.strip().splitlines()
    rendered = ["-- Informal statement:"]
    rendered.extend(f"-- {line}" if line else "--" for line in lines)
    return "\n".join(rendered)


def _transform(value: Any, transforms: list[str]) -> str:
    current = value
    for transform in transforms:
        if transform == "json":
            current = json.dumps(current, ensure_ascii=False, separators=(",", ":"))
            continue
        if not isinstance(current, str):
            raise TestTemplateError(f"transform {transform!r} requires a string value")
        if transform == "strip":
            current = current.strip()
        elif transform == "rstrip":
            current = current.rstrip()
        elif transform == "lean_comment":
            current = _lean_comment(current)
    if not isinstance(current, str):
        if isinstance(current, (int, float, bool)):
            return str(current)
        raise TestTemplateError("mapped values must be strings unless the `json` transform is used")
    return current


def _heading_matches(text: str, heading: str, *, case_sensitive: bool) -> list[re.Match[str]]:
    flags = re.MULTILINE if case_sensitive else re.MULTILINE | re.IGNORECASE
    return list(re.finditer(rf"^[ \t]*{re.escape(heading)}[ \t]*$", text, flags))


def _language_matches(actual: str, allowed: list[str], *, case_sensitive: bool) -> bool:
    actual = actual.strip()
    if case_sensitive:
        return actual in allowed
    lowered = {language.lower() for language in allowed}
    return actual.lower() in lowered


def _normalize_code(code: str) -> str:
    code = code.strip()
    if not code:
        raise TestTemplateError("the extracted Lean code block is empty")
    return code + "\n"


class TestTemplate:
    """A validated test template ready to render and parse."""

    def __init__(self, settings: TestTemplateSettings, *, source: Path):
        self.settings = settings
        self.source = source

    @property
    def id(self) -> str:
        return self.settings.id

    def render(self, record: Any) -> list[dict[str, str]]:
        context: dict[str, str] = {}
        for name, mapping in self.settings.fields.items():
            try:
                value = _resolve_source(mapping.source, record)
            except KeyError as exc:
                if mapping.required:
                    raise TestTemplateError(
                        f"template field {name!r} cannot resolve source {mapping.source!r}"
                    ) from exc
                value = mapping.default
            if value is None:
                if mapping.required:
                    raise TestTemplateError(f"template field {name!r} resolved to null")
                value = mapping.default
            context[name] = _transform(value, mapping.transforms)

        rendered: list[dict[str, str]] = []
        for message in self.settings.messages:
            try:
                content = Template(message.content).substitute(context)
            except (KeyError, ValueError) as exc:
                raise TestTemplateError(
                    f"cannot render {message.role!r} message in template {self.id!r}: {exc}"
                ) from exc
            rendered.append({"role": message.role, "content": content})
        return rendered

    def _extract_with(self, response: str, extractor: ExtractorSettings) -> str:
        if extractor.type == "raw_lean":
            if "```" in response or not _LEAN_START_RE.match(response):
                raise TestTemplateError("response is not recognisable as raw Lean source")
            return _normalize_code(response)

        search_text = response
        if extractor.type.startswith("section_"):
            for required in extractor.required_headings:
                matches = _heading_matches(
                    response, required, case_sensitive=extractor.case_sensitive
                )
                if len(matches) != 1:
                    raise TestTemplateError(
                        f"required heading {required!r} must occur exactly once"
                    )
            assert extractor.heading is not None
            headings = _heading_matches(
                response, extractor.heading, case_sensitive=extractor.case_sensitive
            )
            if len(headings) != 1:
                raise TestTemplateError(
                    f"answer heading {extractor.heading!r} must occur exactly once"
                )
            answer_heading = headings[0]
            for required in extractor.required_headings:
                if _heading_matches(
                    response, required, case_sensitive=extractor.case_sensitive
                )[0].start() >= answer_heading.start():
                    raise TestTemplateError(
                        f"required heading {required!r} must precede the answer heading"
                    )
            search_text = response[answer_heading.end() :]

        if extractor.type == "section_raw_lean":
            if "```" in search_text or not _LEAN_START_RE.match(search_text):
                raise TestTemplateError(
                    "answer section is not recognisable as raw Lean source"
                )
            return _normalize_code(search_text)

        fence_pattern = (
            _FENCE_RE if extractor.type == "section_fenced_code" else _LOOSE_FENCE_RE
        )
        matches = [
            match
            for match in fence_pattern.finditer(search_text)
            if _language_matches(
                match.group("language"),
                extractor.languages,
                case_sensitive=extractor.case_sensitive,
            )
        ]
        if not matches:
            raise TestTemplateError("no matching closed code fence was found")

        if extractor.type == "section_fenced_code":
            match = matches[0]
            if search_text[: match.start()].strip():
                raise TestTemplateError(
                    "non-whitespace content appears before the final code fence"
                )
            if len(matches) != 1:
                raise TestTemplateError("the answer section contains multiple matching code fences")
        else:
            match = matches[-1]

        if extractor.require_terminal and search_text[match.end() :].strip():
            raise TestTemplateError("non-whitespace content follows the final code fence")
        return _normalize_code(match.group("code"))

    def extract(self, response: str, *, repair: bool = False) -> ExtractionResult:
        errors: list[str] = []
        primary = (
            self.settings.output.repair_primary or self.settings.output.primary
            if repair else self.settings.output.primary
        )
        extractors = [primary, *self.settings.output.fallbacks]
        for index, extractor in enumerate(extractors):
            strategy = extractor.name or extractor.type
            try:
                code = self._extract_with(response, extractor)
            except TestTemplateError as exc:
                errors.append(f"{strategy}: {exc}")
                continue
            return ExtractionResult(
                code=code,
                strategy=strategy,
                used_fallback=index > 0,
            )
        raise TestTemplateError(
            "model response did not match the test template or any fallback: "
            + "; ".join(errors)
        )


def resolve_test_template_path(reference: str | Path) -> Path:
    """Resolve a built-in test-template ID or an explicit YAML path."""
    text = str(reference)
    path = BUILTIN_TEST_TEMPLATES.get(text, Path(text))
    if not path.is_file():
        choices = ", ".join(sorted(BUILTIN_TEST_TEMPLATES))
        raise TestTemplateError(
            f"test template does not exist: {reference} (built-ins: {choices})"
        )
    return path.resolve()


def load_test_template(reference: str | Path) -> TestTemplate:
    """Load and validate a test template independently from app configuration."""
    path = resolve_test_template_path(reference)
    # `${name}` belongs to the test-template renderer, not OmegaConf's
    # interpolation language, so it must remain unresolved while loading.
    payload = OmegaConf.to_container(OmegaConf.load(path), resolve=False)
    if not isinstance(payload, dict):
        raise TestTemplateError(f"test template root must be a mapping: {path}")
    try:
        settings = TestTemplateSettings.model_validate(payload)
    except ValueError as exc:
        raise TestTemplateError(f"invalid test template {path}: {exc}") from exc
    return TestTemplate(settings, source=path)
