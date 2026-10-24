from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


DeliveryScope = Annotated[list[Literal['content', 'images']], Field(max_length=2)]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ArtifactInput(Strict):
    kind: Literal['run', 'poster', 'design']
    id: str = Field(min_length=1, max_length=120)
    version: int | None = Field(default=None, ge=1)


class ArtifactLink(ArtifactInput):
    expected_version: int | None = Field(default=None, ge=1)


class ConversationInput(Strict):
    title: str = Field(default='新的创作', max_length=100)


class TurnInput(Strict):
    text: str = Field(default='', max_length=6000)
    media_ids: list[str] = Field(default_factory=list, max_length=8)
    client_turn_id: str = Field(min_length=8, max_length=100)
    reply_to_artifact: ArtifactInput | None = None
    expected_version: int | None = Field(default=None, ge=1)


class QuestionOption(Strict):
    value: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)


class Question(Strict):
    field: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=300)
    options: list[QuestionOption] = Field(default_factory=list, max_length=6)


class Decision(Strict):
    action: Literal['tool', 'reply', 'clarify']
    delivery_scope: DeliveryScope | None = None
    tool: Literal['read_catalog', 'read_product', 'read_media', 'read_history',
                  'read_memory', 'remember', 'read_work_signals', 'prepare_work_brief', 'withdraw_work_signal',
                  'search_signals', 'research_store', 'ground_store', 'prepare_opportunity', 'generate_content',
                  'revise_content', 'create_poster', 'create_design_bundle', 'revise_design', 'read_artifact',
                  'read_price_rules', 'prepare_quote'] | None = None
    arguments: dict = Field(default_factory=dict)
    message: str = Field(default='', max_length=2000)
    questions: list[Question] = Field(default_factory=list, max_length=2)

    @model_validator(mode='before')
    @classmethod
    def project_legacy_delivery_scope(cls, value):
        """Accept the one known misplaced field without weakening tool arguments.

        Scope belongs to the decision, never a tool's authority or payload.
        Validate both representations before projection; conflicting scopes and
        every other unknown argument remain errors rather than being discarded.
        """
        if not isinstance(value, dict) or value.get('action') != 'tool':
            return value
        arguments = value.get('arguments')
        if not isinstance(arguments, dict) or 'delivery_scope' not in arguments:
            return value
        scope = TypeAdapter(DeliveryScope).validate_python(arguments['delivery_scope'])
        if value.get('delivery_scope') is not None:
            outer = TypeAdapter(DeliveryScope).validate_python(value['delivery_scope'])
            if set(outer) != set(scope):
                raise ValueError('Decision.delivery_scope conflicts with arguments.delivery_scope')
        return {**value, 'delivery_scope': scope,
            'arguments': {key: item for key, item in arguments.items() if key != 'delivery_scope'}}
