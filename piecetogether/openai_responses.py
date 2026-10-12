"""Static OpenAI Responses multimodal adapter; provider URLs and files are never retained."""

import base64
import http.client
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .proposals import SemanticProposal

if TYPE_CHECKING:
    from .context import ContextPack
    from .core import Communication


@dataclass(frozen=True)
class OpenAIResponsesConfig:
    model_id: str
    secret_reference: str
    timeout_seconds: float = 30
    max_response_bytes: int = 1_048_576
    max_media_items: int = 4
    max_media_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if (not isinstance(self.model_id, str)
                or not re.fullmatch(r'gpt-[A-Za-z0-9.-]+-\d{4}-\d{2}-\d{2}', self.model_id)
                or not isinstance(self.secret_reference, str) or not self.secret_reference.strip()
                or type(self.timeout_seconds) not in (int, float) or not 0 < self.timeout_seconds <= 300
                or type(self.max_response_bytes) is not int or not 1 <= self.max_response_bytes <= 1_048_576
                or type(self.max_media_items) is not int or not 1 <= self.max_media_items <= 32
                or type(self.max_media_bytes) is not int or not 1 <= self.max_media_bytes <= 1_048_576):
            raise ValueError('invalid static OpenAI Responses configuration')

    @classmethod
    def from_dict(cls, data: Any) -> 'OpenAIResponsesConfig':
        if not isinstance(data, dict):
            raise ValueError('openai must be a static model object')
        try:
            return cls(**data)
        except TypeError as error:
            raise ValueError('invalid static OpenAI Responses configuration') from error


class OpenAIResponsesModel:
    """One opt-in model; operational failures raise OSError and never select another model."""

    accepts_media = True

    def __init__(self, config: OpenAIResponsesConfig, api_key: str):
        if not isinstance(api_key, str) or not api_key:
            raise ValueError('OpenAI secret reference is unresolved')
        self.config, self._api_key = config, api_key

    def propose(self, inbound: 'Communication', contract_version: str,
                context_pack: 'ContextPack') -> SemanticProposal:
        media = context_pack.media
        if (len(media) > self.config.max_media_items
                or sum(item.size for item in media) > self.config.max_media_bytes
                or any(item.media_type not in ('image/png', 'image/jpeg', 'application/pdf')
                       or len(item.content) != item.size for item in media)):
            raise OSError('multimodal input exceeds configured bounds')
        proposal_schema = {
            'schema_version': 1, 'contract_version': contract_version,
            'communication_id': inbound.id, 'candidate_claims': [
                {'source_communication_id': inbound.id, 'interpretation': 'string', 'status': 'candidate'}],
            'draft_response': 'string', 'intent': 'candidate', 'semantic_revision': None, 'operations': [
                {'kind': 'entity', 'id': 'local-id', 'entity_type': 'Contract type',
                 'attributes': {}, 'action': 'create'},
                {'kind': 'claim', 'id': 'local-id', 'target_id': 'entity local-id',
                 'concept': 'Contract concept', 'value': 'Contract value',
                 'grounding_policy': 'Contract policy', 'artifact_id': 'attachment-1'},
                {'kind': 'grounding_plan', 'policy': 'Contract policy', 'claim_ids': ['local-id'],
                 'resolution_ids': [], 'acceptance_mode': 'explicit'},
                {'kind': 'artifact', 'id': 'attachment-1', 'artifact_type': 'permitted media artifact type',
                 'roles': ['permitted role'], 'action': 'classify'},
                {'kind': 'grounding_resolution', 'item_id': 'pending Grounding item ID from context.records',
                 'policy': 'Contract policy', 'evidence_communication_id': inbound.id,
                 'acceptance_mode': 'explicit', 'outcome': 'accepted', 'rationale': None,
                 'evidence_span': None, 'successor_ids': []},
            ],
        }
        semantic_commit_example = {
            'schema_version': 1, 'contract_version': contract_version,
            'communication_id': inbound.id, 'candidate_claims': [], 'draft_response': 'string',
            'intent': 'semantic_commit', 'semantic_revision': context_pack.semantic_revision,
            'operations': [{'kind': 'grounding_resolution', 'item_id': 'pending item ID',
                'policy': 'Contract policy', 'evidence_communication_id': inbound.id,
                'acceptance_mode': 'explicit', 'outcome': 'accepted', 'rationale': None,
                'evidence_span': None, 'successor_ids': []}],
        }
        instruction = {
            'instruction': ('Return exactly one JSON SemanticProposal matching proposal_schema. Do not invent '
                            'missing or ambiguous values. Use only Contract names and attachment IDs supplied in '
                            'context.media. Artifact observations are candidate ClaimOperations with artifact_id and '
                            'a GroundingPlanOperation; initial observations are never grounded, authoritative, or persisted by you. '
                            'On a later sender contribution, you may propose a semantic_commit only with pending '
                            'Grounding item IDs from context.records, the current context semantic_revision, and '
                            'that contribution as evidence; Core validates Actor, policy, chronology and commits.'),
            'proposal_schema': proposal_schema,
            'semantic_commit_example': semantic_commit_example,
            'communication': {'id': inbound.id, 'text': inbound.text},
            'contract_version': contract_version,
            'context': context_pack.captured(),
        }
        input_content: list[dict[str, str]] = [{
            'type': 'input_text', 'text': json.dumps(instruction, ensure_ascii=False, sort_keys=True),
        }]
        for item in media:
            encoded = base64.b64encode(item.content).decode('ascii')
            if item.media_type == 'application/pdf':
                input_content.append({'type': 'input_file', 'filename': 'artifact.pdf',
                                      'file_data': 'data:application/pdf;base64,' + encoded})
            else:
                input_content.append({'type': 'input_image',
                                      'image_url': f'data:{item.media_type};base64,' + encoded})
        request = {'model': self.config.model_id, 'store': False,
                   'input': [{'role': 'user', 'content': input_content}]}
        connection = http.client.HTTPSConnection('api.openai.com', timeout=self.config.timeout_seconds)
        try:
            body = json.dumps(request, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            connection.request('POST', '/v1/responses', body, {
                'Authorization': 'Bearer ' + self._api_key,
                'Content-Type': 'application/json',
            })
            response = connection.getresponse()
            raw = response.read(self.config.max_response_bytes + 1)
            if response.status != 200 or len(raw) > self.config.max_response_bytes:
                raise OSError('OpenAI Responses request failed')
            decoded = json.loads(raw)
            if not isinstance(decoded, dict) or not isinstance(decoded.get('output'), list):
                raise OSError('OpenAI Responses response is invalid')
            text = next((part.get('text') for output in decoded['output'] if isinstance(output, dict)
                         for part in output.get('content', []) if isinstance(part, dict)
                         and part.get('type') == 'output_text' and isinstance(part.get('text'), str)), None)
            if text is None or len(text.encode()) > self.config.max_response_bytes:
                raise OSError('OpenAI Responses output is unavailable')
            proposal = SemanticProposal.from_dict(json.loads(text))
            if proposal.communication_id != inbound.id or proposal.contract_version != contract_version:
                raise OSError('OpenAI Responses proposal provenance is invalid')
            return proposal
        except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError, UnicodeError):
            raise OSError('OpenAI Responses unavailable') from None
        finally:
            try:
                connection.close()
            except OSError:
                pass
